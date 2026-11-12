"""Differencing guard logic: when two filter sets are nested, so their counts can be subtracted."""

from datetime import date
from itertools import pairwise

import pytest

from txlens_backend.services import cohorts
from txlens_backend.services.disclosure import add, implies, interval, overlapping, refines

AGE_18 = {"age": interval(lo=18)}
AGE_19 = {"age": interval(lo=19)}


def lab(value: float, op: str = ">=", window: tuple[int, int] = (0, 365)) -> dict[str, object]:
    rule = cohorts.LabRule(lab="dd_cfdna", op=op, value=value, days_from_transplant=window)  # type: ignore[arg-type]
    return {"labs": {"exists": [cohorts.lab_atom(rule)]}}


@pytest.mark.parametrize(
    ("narrow", "wide"),
    [
        (AGE_19, AGE_18),
        ({"age": interval(lo=18, hi=18)}, AGE_18),
        ({"age": interval(lo=18, lo_inclusive=False)}, AGE_18),
        (AGE_18 | {"organ": {"eq": "kidney"}}, AGE_18),
        ({"organ": {"eq": "kidney"}}, {}),
        ({"conditions": {"any": [1]}}, {"conditions": {"any": [1, 2]}}),
        (lab(1.0), lab(0.5)),
        (lab(0.5, window=(30, 90)), lab(0.5)),
        (lab(0.5, op=">"), lab(0.5)),
        (lab(0.2, op="<="), lab(0.3, op="<")),
        ({"labs": {"exists": [*lab(1.0)["labs"]["exists"], *lab(0.5, "<=")["labs"]["exists"]]}},  # type: ignore[index]
         lab(0.5)),
        ({"d": interval("2024-01-01", "2024-12-31")}, {"d": interval(lo="2023-01-01")}),
        ({"atom:x IS NULL": {"atom": True}, "organ": {"eq": "heart"}},
         {"atom:x IS NULL": {"atom": True}}),
    ],
)  # fmt: skip
def test_narrower_filters_refine_wider_ones(narrow: dict, wide: dict) -> None:  # type: ignore[type-arg]
    assert refines(narrow, wide)
    assert overlapping(narrow, wide) and overlapping(wide, narrow)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ({"organ": {"eq": "kidney"}}, {"organ": {"eq": "heart"}}),
        ({"age": interval(lo=18, hi=30)}, {"age": interval(lo=25, hi=40)}),
        (AGE_18 | {"organ": {"eq": "kidney"}}, AGE_19 | {"donor_type": {"eq": "living"}}),
        ({"conditions": {"any": [1]}}, {"conditions": {"any": [2]}}),
        (lab(1.0), lab(1.0, op="<=")),
        (lab(1.0, window=(0, 30)), lab(1.0, window=(20, 60))),
        ({"atom:a": {"atom": True}}, {"atom:b": {"atom": True}}),
    ],
)
def test_crossing_filters_do_not_overlap(a: dict, b: dict) -> None:  # type: ignore[type-arg]
    assert not overlapping(a, b)


def test_identical_filters_overlap() -> None:
    assert overlapping(AGE_18, AGE_18) and overlapping({}, {})


def test_ranges_on_one_dimension_are_conjoined() -> None:
    both = add({"age": interval(lo=18)}, "age", interval(hi=30))
    assert both == {"age": interval(18, 30)}
    assert add(both, "age", interval(lo=20))["age"] == interval(20, 30)
    assert implies(add(AGE_18, "age", interval(lo=19))["age"], AGE_19["age"])


def test_each_attrition_step_adds_its_rule() -> None:
    rules = cohorts.CohortRules(
        organ="kidney", age_min=18, age_max=70,
        transplant_date=cohorts.DateRange(start=date(2020, 1, 1)),
        lab_rules=[cohorts.LabRule(lab="dd_cfdna", op=">=", value=1.0)],
    )  # fmt: skip
    steps = cohorts.step_constraints(rules)
    assert len(steps) == 1 + len(cohorts.compile_rules(rules).steps)
    assert steps[0] == {}
    assert steps[1] == {"organ": {"eq": "kidney"}}
    assert steps[3]["age"] == interval(18, 70)
    assert steps[4]["transplant_date"] == interval(lo="2020-01-01")
    assert all(refines(later, earlier) for earlier, later in pairwise(steps))
