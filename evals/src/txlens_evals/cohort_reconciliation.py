"""Reconcile cohort rules on the pipeline-loaded database with the generator's ground truth.

`make cohort-report` runs each rule through the API's own cohort service, as the app login
(Row-Level Security on, one tenant at a time). It compares the members, matched by
(center, MRN) through app.patient_identity, with the patients the ground truth says
should qualify. It writes evals/reports/cohort_rules.md. Any cohort number in the README
comes from that report.

Lab results the generator deliberately corrupted (bad date, missing value) are quarantined
by the pipeline, so they are left out of the expected answer, just as a correct pipeline
leaves them out of OMOP.
"""

import argparse
import csv
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import Engine, text

from txlens_backend.config import get_settings
from txlens_backend.db import check_role_is_rls_bound, make_engine, tenant_transaction
from txlens_backend.db_setup import center_tenants
from txlens_backend.services.cohorts import CohortRules, DateRange, LabRule, member_ids

BAD_ROW_ISSUES = {"bad_date", "missing_value"}
Patient = dict[str, str]


@dataclass(frozen=True)
class Truth:
    """Ground-truth patients plus the facts the rules need."""

    patients: list[Patient]
    high_dd_cfdna_year_one: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class Case:
    """A cohort rule and who should match it according to the ground truth."""

    name: str
    rules: CohortRules
    expected: Callable[[Truth, Patient], bool]
    known_gap: str = ""  # why the database cannot match yet (reported, not counted as a pass)


@dataclass(frozen=True)
class Result:
    """Expected vs actual members of one rule, all centers together."""

    name: str
    expected: int
    actual: int
    missing: int
    extra: int
    by_center: str = ""  # 'center: actual/expected' per center
    known_gap: str = ""

    @property
    def passed(self) -> bool:
        return self.missing == 0 and self.extra == 0


def read_csv(folder: Path, name: str) -> list[dict[str, str]]:
    with (folder / f"{name}.csv").open() as f:
        return list(csv.DictReader(f))


def load_truth(folder: Path) -> Truth:
    """Patients, and who had a clean dd-cfDNA >= 1% within 365 days after transplant."""
    patients = read_csv(folder, "patients")
    bad = {r["lab_id"] for r in read_csv(folder, "lab_feed_issues") if r["issue"] in BAD_ROW_ISSUES}
    transplant = {p["patient_id"]: date.fromisoformat(p["transplant_date"]) for p in patients}
    high = {
        lab["patient_id"]
        for lab in read_csv(folder, "labs")
        if lab["lab"] == "dd_cfdna"
        and lab["lab_id"] not in bad
        and float(lab["value"]) >= 1.0
        and timedelta(0)
        <= datetime.fromisoformat(lab["collected_at"]).date() - transplant[lab["patient_id"]]
        <= timedelta(days=365)
    }
    return Truth(patients, high)


def age(p: Patient) -> int:
    """Age at transplant from years only (OMOP keeps only the birth year)."""
    return int(p["transplant_date"][:4]) - int(p["birth_date"][:4])


DIABETES_MELLITUS = 201820  # SNOMED 73211009; patients carry the descendant "type 2 diabetes"
DD_CFDNA_YEAR_ONE = LabRule(lab="dd_cfdna", op=">=", value=1.0, days_from_transplant=(0, 365))
CASES = [
    Case("kidney, dd-cfDNA >= 1% within 365 days of transplant",
         CohortRules(organ="kidney", lab_rules=[DD_CFDNA_YEAR_ONE]),
         lambda t, p: p["organ"] == "kidney" and p["patient_id"] in t.high_dd_cfdna_year_one),
    Case("any organ, dd-cfDNA >= 1% within 365 days of transplant",
         CohortRules(lab_rules=[DD_CFDNA_YEAR_ONE]),
         lambda t, p: p["patient_id"] in t.high_dd_cfdna_year_one),
    Case("kidney, living donor", CohortRules(organ="kidney", donor_type="living"),
         lambda t, p: p["organ"] == "kidney" and p["donor_type"] == "living"),
    Case("kidney, deceased donor", CohortRules(organ="kidney", donor_type="deceased"),
         lambda t, p: p["organ"] == "kidney" and p["donor_type"] == "deceased"),
    Case("heart", CohortRules(organ="heart"), lambda t, p: p["organ"] == "heart"),
    Case("age at transplant 40-59", CohortRules(age_min=40, age_max=59),
         lambda t, p: 40 <= age(p) <= 59),
    Case("transplant in 2024 (whole year)",
         CohortRules(transplant_date=DateRange(start=date(2024, 1, 1), end=date(2024, 12, 31))),
         lambda t, p: p["transplant_date"].startswith("2024")),
    # Exact since Step 2.2 mapped free-text conditions (it found 428 of 820 on codes only).
    # Extra members would show family-history diabetes leaking in from note NLP.
    Case("any diabetes mellitus (73211009 and descendants)",
         CohortRules(condition_concept_ids=[DIABETES_MELLITUS]),
         lambda t, p: p["diabetes"] == "true"),
]  # fmt: skip


def actual_members(engine: Engine, rules: CohortRules, tenants: list[str]) -> set[tuple[str, str]]:
    """(center, MRN) of every cohort member, center by center as the app login."""
    found = set()
    for tenant in tenants:
        with tenant_transaction(engine, tenant) as conn:
            ids = member_ids(conn, rules)
            mrns = conn.execute(
                text("SELECT mrn FROM app.patient_identity WHERE person_id = ANY(:ids)"),
                {"ids": ids},
            ).scalars()
            found |= {(tenant, str(m)) for m in mrns}
    return found


def reconcile(engine: Engine, truth: Truth, tenants: list[str]) -> list[Result]:
    """Every case: expected vs actual members."""
    results = []
    for case in CASES:
        expected = {(p["tenant_id"], p["mrn"]) for p in truth.patients if case.expected(truth, p)}
        actual = actual_members(engine, case.rules, tenants)
        by_center = ", ".join(
            f"{t}: {sum(a[0] == t for a in actual)}/{sum(e[0] == t for e in expected)}"
            for t in tenants
        )
        results.append(
            Result(case.name, len(expected), len(actual), len(expected - actual),
                   len(actual - expected), by_center, case.known_gap)
        )  # fmt: skip
    return results


def render(results: list[Result], n_patients: int) -> str:
    """The report as markdown."""
    exact = [r for r in results if not r.known_gap]
    gaps = [r for r in results if r.known_gap]
    passed = sum(r.passed for r in exact)
    lines = [
        "# Cohort rules: database vs ground truth",
        "",
        f"Generated {date.today().isoformat()} by `make cohort-report` "
        "(evals/src/txlens_evals/cohort_reconciliation.py). Synthetic data only.",
        "",
        f"Each rule runs through the API's cohort service on the pipeline-loaded database "
        f"({n_patients:,} patients, all centers, Row-Level Security on). Members are matched "
        "to the ground truth by (center, MRN).",
        "",
        f"**{passed} of {len(exact)} rules match the ground truth exactly.**",
        "",
        "| Rule | Expected | Actual | Missing | Extra | Result |",
        "|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {r.name} | {r.expected:,} | {r.actual:,} | {r.missing:,} | {r.extra:,} | "
        f"{'PASS' if r.passed else 'FAIL'} |"
        for r in exact
    ]
    if gaps:
        lines += ["", "## Known gaps (data not yet mapped upstream)", "",
                  "| Rule | Expected | Actual | Per center (actual/expected) | Why |",
                  "|---|---|---|---|---|"]  # fmt: skip
        lines += [
            f"| {r.name} | {r.expected:,} | {r.actual:,} | {r.by_center} | {r.known_gap} |"
            for r in gaps
        ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Run all cases and write the report; exit 1 if any rule (other than a known gap) differs."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--truth", type=Path, default=Path("data/ground_truth"))
    parser.add_argument("--out", type=Path, default=Path("evals/reports/cohort_rules.md"))
    args = parser.parse_args(argv)
    engine = make_engine(get_settings().app_database_url.get_secret_value())
    check_role_is_rls_bound(engine)
    truth = load_truth(args.truth)
    results = reconcile(engine, truth, list(center_tenants()))
    engine.dispose()
    report = render(results, len(truth.patients))
    args.out.write_text(report)
    print(report)
    return 0 if all(r.passed for r in results if not r.known_gap) else 1


if __name__ == "__main__":
    raise SystemExit(main())
