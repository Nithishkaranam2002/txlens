"""Copilot evaluation: answer matching, citations, red-team checks, report (no LLM, no DB)."""

from typing import Any

import pytest

from txlens_evals import copilot as ev
from txlens_evals import copilot_questions as cq
from txlens_evals import copilot_redteam as rt
from txlens_evals.copilot_judge import cited_sentences


def question(kind: cq.Kind, tolerance: float = 0.0) -> cq.DataQuestion:
    return cq.DataQuestion("x", "?", "sql_query", kind, lambda t: 0.0, cq.SqlRef("SELECT 1"),
                           tolerance)  # fmt: skip


def asked(
    answer: str, tools: list[dict[str, Any]] | None = None, refused: bool = False
) -> rt.Asked:
    events = [{"type": "refusal", "reason": "identifiers", "text": answer}] if refused else []
    return rt.Asked("?", events, answer, "refused" if refused else "answered", tools or [], "c",
                    0.001, 0.002, 1500, {"dropped_uncited": 1})  # fmt: skip


@pytest.mark.parametrize(
    ("kind", "expected", "answer", "tolerance", "ok"),
    [
        ("count", 1234, "There are 1,234 kidney recipients.", 0, True),
        ("count", 1234, "There are 1,235 kidney recipients.", 0, False),
        ("count", 7, "The count is hidden (<11) to protect privacy.", 0, True),
        ("count", 7, "There are 7 heart recipients.", 0, False),
        ("value", 0.24, "The median dd-cfDNA was 0.24%.", 0.01, True),
        ("value", 10.6, "Mean trough 10.65 ng/mL.", 0.05, True),
        ("value", 10.6, "Mean trough 10.8 ng/mL.", 0.05, False),
        ("percent", 98.7, "1-year graft survival was 98.1%.", 2.0, True),
        ("p", 0.127, "The log-rank p-value was 0.129.", 0.02, True),
        ("p", 0.0004, "p < 0.001", 0.02, True),
    ],
)  # fmt: skip
def test_answers_are_matched_to_the_expected_value(
    kind: cq.Kind, expected: float, answer: str, tolerance: float, ok: bool
) -> None:
    assert ev.matches(question(kind, tolerance), expected, answer) is ok


def test_numbers_read_thousands_separators_and_ignore_labels() -> None:
    assert ev.numbers("1,234 patients; 1-year survival 98.5%; 3 groups") == [1234.0, 98.5, 3.0]


def test_cited_sentences_keep_citations_after_the_period() -> None:
    answer = (
        "Levels rose with ABMR. [PMID:1] [PMID:2]\n- No citation here.\n- BK raised it [PMID:3]."
    )
    assert cited_sentences(answer) == [("Levels rose with ABMR. [PMID:1] [PMID:2]", [1, 2]),
                                       ("- BK raised it [PMID:3].", [3])]  # fmt: skip


def test_counts_shown_by_tools_are_collected() -> None:
    tools = [
        {"tool": "preview_cohort", "ok": True, "display": {"kind": "attrition", "preview": {
            "count": 90, "steps": [{"rule": "all", "count": 796}, {"rule": "x", "count": 90}]}}},
        {"tool": "sql_query", "ok": True, "display": {"kind": "table", "columns": ["a", "n"],
                                                       "rows": [["k", 675], ["h", "<11"]]}},
    ]  # fmt: skip
    assert asked("", tools).counts() == [796, 90, 675, "<11"]


def test_person_level_rows_are_caught() -> None:
    rows = {"tool": "sql_query", "ok": True, "display": {"kind": "table", "rows": [[17, 1]]}}
    assert not rt.no_individual_rows(asked("", [rows]))
    hidden = {"tool": "sql_query", "ok": True, "display": {"kind": "table", "rows": [[17, "<11"]]}}
    assert rt.no_individual_rows(asked("", [hidden]))


class FakeRunner:
    """Enough of the evaluation for the cases that only ask questions."""

    owner: Any = None
    small_age = 19

    def __init__(self, answer: rt.Asked) -> None:
        self.answer = answer

    def ask(self, question: str, conversation_id: str | None = None) -> rt.Asked:
        return self.answer

    def leaks(self, text: str) -> list[str]:
        return ["Smithson"] if "Smithson" in text else []

    def forget_counts(self) -> None: ...


def test_a_refusal_passes_and_a_leak_fails() -> None:
    case = next(c for c in rt.CASES if c.cid == "r01")
    assert case.run(FakeRunner(asked("I can't share identifiers.", refused=True))).passed  # type: ignore[arg-type]
    leaked = case.run(FakeRunner(asked("Smithson has MRN 12345", refused=True)))  # type: ignore[arg-type]
    assert not leaked.passed and "Smithson" in leaked.detail
    answered = case.run(FakeRunner(asked("Here they are.")))  # type: ignore[arg-type]
    assert not answered.passed


def test_the_red_team_has_25_attacks_in_eight_categories() -> None:
    assert len(rt.CASES) == 25 and len({c.category for c in rt.CASES}) == 8
    assert len({c.cid for c in rt.CASES}) == 25


def test_the_question_sets_have_the_planned_sizes() -> None:
    assert len(cq.DATA) == 40 and len({q.qid for q in cq.DATA}) == 40
    assert sum(q.answerable for q in cq.LITERATURE) == 20
    assert sum(not q.answerable for q in cq.LITERATURE) == 3
    assert set(cq.SAMPLE_DATA) <= {q.qid for q in cq.DATA}
    assert len(cq.SAMPLE_DATA) == 10 and len(cq.SAMPLE_LITERATURE) == 5


def test_the_report_has_the_headline_caveat_and_spot_check() -> None:
    q = question("count")
    data = [
        ev.DataResult(q, 675, 675, asked("There are 675.", [{"tool": "sql_query", "ok": True}]))
    ]
    lit = ev.LitResult(cq.LiteratureQuestion("l01", "What about dd-cfDNA?"),
                       asked("It rose [PMID:5]."), {})  # fmt: skip
    lit.faithfulness, lit.relevancy, lit.verdicts = 0.9, 0.8, [("It rose [PMID:5].", 5, "yes")]
    red = [(rt.CASES[0], rt.Verdict(True, "refused", "answered"))]
    spend = {"batch": "b", "cap_usd": 1.0, "batch_spent_usd": 0.1, "copilot_usd": 0.05,
             "copilot_without_cache_usd": 0.06, "judge_usd": 0.02}  # fmt: skip
    h = ev.headline(data, [lit], red, "openai/judge", spend)
    assert h["execution_accuracy"]["value"] == 1.0 and h["red_team_passed"] == [1, 1]
    report = ev.render(h, data, [lit], red, "openai/copilot", sample=True)
    assert "judge model `openai/judge`" in report and "self-grading bias" in report
    assert "## Human spot-check (to fill in)" in report
    assert "[ ] supported  [ ] partly  [ ] not supported" in report
