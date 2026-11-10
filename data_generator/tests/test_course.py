import statistics
from datetime import timedelta

from datagen_testutils import fraction

from txlens_datagen.config import GeneratorConfig
from txlens_datagen.models import PatientRecord

THREE_YEARS = timedelta(days=3 * 365)


def year1_rejection(record: PatientRecord) -> bool:
    tx = record.patient.transplant_date
    return any(e.biopsy_date - tx <= timedelta(days=365) for e in record.rejections)


def test_year1_rejection_rate(cohort: list[PatientRecord]) -> None:
    assert 0.10 <= fraction([year1_rejection(r) for r in cohort]) <= 0.15
    kinds = {e.rejection_type for r in cohort for e in r.rejections}
    assert kinds == {"borderline", "TCMR", "ABMR", "mixed"}


def test_graft_loss_rate_by_three_years(
    cohort: list[PatientRecord], config: GeneratorConfig
) -> None:
    full = [r for r in cohort if config.data_cutoff - r.patient.transplant_date >= THREE_YEARS]
    assert 0.04 <= fraction([r.outcome.graft_loss_date is not None for r in full]) <= 0.09


def test_graft_loss_more_likely_after_abmr_or_unresolved(cohort: list[PatientRecord]) -> None:
    def lost(records: list[PatientRecord]) -> float:
        return fraction([r.outcome.graft_loss_date is not None for r in records])

    no_rejection = [r for r in cohort if not r.rejections]
    unresolved = [r for r in cohort if any(not e.responded for e in r.rejections)]
    abmr = [r for r in cohort if any(e.rejection_type == "ABMR" for e in r.rejections)]
    assert lost(unresolved) > 3 * lost(no_rejection)
    assert lost(abmr) > lost(no_rejection)


def test_most_rejections_respond(cohort: list[PatientRecord]) -> None:
    episodes = [e for r in cohort for e in r.rejections]
    assert 0.65 <= fraction([e.responded for e in episodes]) <= 0.88


def test_some_deaths_with_functioning_graft(cohort: list[PatientRecord]) -> None:
    rate = fraction([r.outcome.death_with_functioning_graft for r in cohort])
    assert 0.005 <= rate <= 0.05


def test_dd_cfdna_peak_order_by_rejection_type(cohort: list[PatientRecord]) -> None:
    def mean_peak(kind: str) -> float:
        return statistics.mean(
            e.peak_dd_cfdna_pct
            for r in cohort
            if r.patient.organ == "kidney"
            for e in r.rejections
            if e.rejection_type == kind
        )

    assert mean_peak("ABMR") > mean_peak("TCMR") > mean_peak("borderline")
    assert mean_peak("borderline") < 1.0


def test_dsa_more_common_in_abmr(cohort: list[PatientRecord]) -> None:
    episodes = [e for r in cohort if r.patient.organ == "kidney" for e in r.rejections]
    antibody = [e.dsa_positive for e in episodes if e.rejection_type in ("ABMR", "mixed")]
    cellular = [e.dsa_positive for e in episodes if e.rejection_type in ("TCMR", "borderline")]
    assert fraction(antibody) > 0.5 > 0.2 > fraction(cellular)


def test_injury_episodes_are_labelled_false_alarms(cohort: list[PatientRecord]) -> None:
    injuries = [(r, e) for r in cohort for e in r.injuries]
    assert {e.cause for _, e in injuries} == {
        "bk_nephropathy",
        "cmv_infection",
        "acute_tubular_injury",
    }
    assert 0.02 <= len(injuries) / len(cohort) <= 0.08
    for record, episode in injuries:
        assert record.patient.organ == "kidney"
        assert not episode.is_rejection
        biopsy = next(b for b in record.biopsies if b.biopsy_id == episode.biopsy_id)
        assert biopsy.diagnosis == "no_rejection"
        assert biopsy.other_finding


def test_rejection_biopsy_matches_episode(cohort: list[PatientRecord]) -> None:
    for record in cohort:
        for episode in record.rejections:
            biopsy = next(b for b in record.biopsies if b.biopsy_id == episode.biopsy_id)
            assert biopsy.diagnosis == episode.rejection_type
            assert biopsy.biopsy_date == episode.biopsy_date
            assert biopsy.indication == "for_cause"


def test_heart_protocol_biopsies_show_no_rejection(cohort: list[PatientRecord]) -> None:
    protocol = [b for r in cohort for b in r.biopsies if b.indication == "protocol"]
    assert protocol
    assert all(b.organ == "heart" and b.ishlt_acr_grade == "0R" for b in protocol)


def test_heart_graft_loss_means_death(cohort: list[PatientRecord]) -> None:
    for record in cohort:
        if record.patient.organ == "heart" and record.outcome.graft_loss_date:
            assert record.outcome.death_date == record.outcome.graft_loss_date
