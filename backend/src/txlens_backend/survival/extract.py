"""OMOP -> one analysis row per transplant recipient (RLS limits it to the caller's center).

Time zero is the first transplant. The outcome is graft loss (a graft-failure condition);
death with a functioning graft is the competing event; follow-up ends at the end of the
patient's observation period. Status: 0 censored, 1 graft loss, 2 death with a functioning
graft. Graft loss wins a tie with death (a failed heart graft is recorded as death that day).

The extract holds intervals in days since transplant and no calendar dates or identifiers.
Exposures that happen during follow-up (DSA at the month-1 test, dd-cfDNA >= 1% in year 1,
rejection) are kept as "first day" columns: landmark.py decides how they may be used.
"""

from collections.abc import Mapping

import numpy as np
import pandas as pd
from sqlalchemy import Connection, text

from txlens_backend.clinical_codes import DSA_LOCAL, LABS
from txlens_backend.rejection import rejection_concept_ids
from txlens_backend.services.facts import FACTS_CTE, FACTS_PARAMS, descendant_ids

CENSORED, GRAFT_LOSS, DEATH = 0, 1, 2
# A DSA test this many days after transplant counts as the month-1 test (first one is day 30).
DSA_WINDOW_DAYS = 30
DSA_POSITIVE_MFI = 1000.0  # the generator's and the surveillance config's positive cut-off
DD_CFDNA_HIGH_PCT = 1.0  # "high risk of rejection" threshold (CLAUDE.md domain facts)
HISTORY_OF_EVENT = ("OMOP Extension", "OMOP5165859")
DGF = ("SNOMED", "426136000")  # delayed renal graft function
ABMR = ("SNOMED", "1381981002")  # antibody-mediated rejection of transplant

EXTRACT_SQL = f"""
WITH {FACTS_CTE},
followup AS (
    SELECT person_id, max(observation_period_end_date) AS end_date
    FROM cdm.observation_period GROUP BY person_id
),
dsa AS (
    SELECT m.person_id, max(m.value_as_number) AS mfi
    FROM cdm.measurement m
    JOIN transplant t ON t.person_id = m.person_id
    JOIN vocab.concept c ON c.concept_id = m.measurement_source_concept_id
    WHERE c.vocabulary_id = :dsa_vocab AND c.concept_code = :dsa_code
      AND m.measurement_date BETWEEN t.transplant_date AND t.transplant_date + :dsa_days
    GROUP BY m.person_id
),
dgf AS (
    SELECT DISTINCT person_id FROM cdm.observation
    WHERE observation_concept_id = :history_of_event AND value_as_concept_id = ANY(:dgf_ids)
),
dd_cfdna_high AS (
    SELECT m.person_id, min(m.measurement_date - t.transplant_date) AS day
    FROM cdm.measurement m
    JOIN transplant t ON t.person_id = m.person_id
    JOIN vocab.concept c ON c.concept_id = m.measurement_concept_id
    WHERE c.vocabulary_id = :dd_vocab AND c.concept_code = :dd_code
      AND m.value_as_number >= :dd_high AND m.measurement_date >= t.transplant_date
    GROUP BY m.person_id
),
rejection AS (
    SELECT co.person_id,
           min(co.condition_start_date - t.transplant_date) AS day,
           min(co.condition_start_date - t.transplant_date) FILTER (
               WHERE co.condition_concept_id = ANY(:abmr_ids)) AS abmr_day
    FROM cdm.condition_occurrence co
    JOIN transplant t ON t.person_id = co.person_id
    WHERE (co.condition_concept_id = ANY(:rejection_ids)
           OR co.condition_source_concept_id = ANY(:rejection_ids))
      AND co.condition_start_date >= t.transplant_date
    GROUP BY co.person_id
)
SELECT f.person_id,
       extract(year FROM f.transplant_date)::int - f.year_of_birth AS age,
       f.organ,
       coalesce(f.donor_type, CASE WHEN f.organ = 'heart' THEN 'deceased' END) AS donor_type,
       g.failure_date - f.transplant_date AS graft_loss_day,
       f.death_date - f.transplant_date AS death_day,
       fu.end_date - f.transplant_date AS end_day,
       dsa.mfi AS dsa_mfi,
       (dgf.person_id IS NOT NULL) AS dgf,
       dd.day AS dd_cfdna_high_day,
       r.day AS rejection_day,
       r.abmr_day
FROM facts f
JOIN followup fu ON fu.person_id = f.person_id
LEFT JOIN graft_failure g ON g.person_id = f.person_id
LEFT JOIN dsa ON dsa.person_id = f.person_id
LEFT JOIN dgf ON dgf.person_id = f.person_id
LEFT JOIN dd_cfdna_high dd ON dd.person_id = f.person_id
LEFT JOIN rejection r ON r.person_id = f.person_id
WHERE f.transplant_date IS NOT NULL
ORDER BY f.person_id
"""


def concept_id(conn: Connection, vocabulary: str, code: str) -> int:
    """The concept id of one code (0 when the vocabulary does not have it)."""
    found = conn.execute(
        text("SELECT concept_id FROM vocab.concept WHERE vocabulary_id = :v AND concept_code = :c"),
        {"v": vocabulary, "c": code},
    ).scalar_one_or_none()
    return int(found) if found is not None else 0


def extract_params(conn: Connection) -> Mapping[str, object]:
    """Bind parameters: codes, thresholds and the concept sets they expand to."""
    dgf, abmr = concept_id(conn, *DGF), concept_id(conn, *ABMR)
    return dict(FACTS_PARAMS) | {
        "dsa_vocab": DSA_LOCAL[0], "dsa_code": DSA_LOCAL[1], "dsa_days": DSA_WINDOW_DAYS,
        "dd_vocab": LABS["dd_cfdna"][0], "dd_code": LABS["dd_cfdna"][1],
        "dd_high": DD_CFDNA_HIGH_PCT,
        "history_of_event": concept_id(conn, *HISTORY_OF_EVENT),
        "dgf_ids": sorted(descendant_ids(conn, [dgf])) if dgf else [],
        "abmr_ids": sorted(descendant_ids(conn, [abmr])) if abmr else [],
        "rejection_ids": sorted(rejection_concept_ids(conn)),
    }  # fmt: skip


def outcome(graft_loss_day: object, death_day: object, end_day: object) -> tuple[int, int]:
    """(time in days, status) from the three days since transplant (graft loss wins ties)."""
    end = int(end_day)  # type: ignore[call-overload]
    loss = None if pd.isna(graft_loss_day) else int(graft_loss_day)  # type: ignore[call-overload]
    death = None if pd.isna(death_day) else int(death_day)  # type: ignore[call-overload]
    if loss is not None and loss <= end and (death is None or loss <= death):
        return loss, GRAFT_LOSS
    if death is not None and death <= end:
        return death, DEATH
    return end, CENSORED


def to_frame(rows: list[Mapping[str, object]]) -> pd.DataFrame:
    """Analysis frame: time/status, covariates and first-exposure days (NaN = never or
    not applicable)."""
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    times = [outcome(r["graft_loss_day"], r["death_day"], r["end_day"]) for r in rows]
    frame["time"] = [t for t, _ in times]
    frame["status"] = [s for _, s in times]
    frame["dsa_positive"] = np.where(
        frame["dsa_mfi"].isna(), np.nan, (frame["dsa_mfi"] >= DSA_POSITIVE_MFI).astype(float)
    )
    # Delayed graft function is a kidney diagnosis: not applicable (NaN) to heart grafts.
    frame["dgf"] = np.where(frame["organ"] == "heart", np.nan, frame["dgf"].astype(float))
    columns = ["person_id", "time", "status", "age", "organ", "donor_type", "dgf",
               "dsa_positive", "dd_cfdna_high_day", "rejection_day", "abmr_day"]  # fmt: skip
    frame = frame[columns]
    return frame[frame["time"] >= 0].reset_index(drop=True)


def extract(conn: Connection) -> pd.DataFrame:
    """One row per transplant recipient of the caller's center (RLS applies)."""
    rows = conn.execute(text(EXTRACT_SQL), extract_params(conn)).mappings().all()
    return to_frame([dict(r) for r in rows])
