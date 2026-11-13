"""The data cut-off: with DATA_CUTOFF set, surveillance runs as of the cut-off when no date is
given (CLI and nightly schedule), and a later date is refused by the CLI and by the activity
itself, whoever started the run."""

from datetime import date
from typing import cast

import pytest
from sqlalchemy import Engine
from temporalio.client import ScheduleActionStartWorkflow
from temporalio.exceptions import ApplicationError

from txlens_backend.config import Settings
from txlens_backend.surveillance.cutoff import AfterCutoffError, check_as_of, pinned_as_of
from txlens_backend.workflows import cli
from txlens_backend.workflows.surveillance import (
    CenterRun,
    SurveillanceActivities,
    SurveillanceRequest,
)

CUTOFF = date(2026, 9, 30)


def test_no_date_runs_as_of_the_cutoff_and_without_one_as_of_today() -> None:
    assert pinned_as_of(None, CUTOFF) == CUTOFF
    assert pinned_as_of(None, None) is None  # the workflow then uses the day it starts


def test_dates_up_to_the_cutoff_run_and_later_ones_are_refused() -> None:
    assert pinned_as_of(date(2026, 6, 30), CUTOFF) == date(2026, 6, 30)
    assert pinned_as_of(CUTOFF, CUTOFF) == CUTOFF
    with pytest.raises(AfterCutoffError, match="after the data cut-off 2026-09-30"):
        pinned_as_of(date(2026, 10, 1), CUTOFF)
    check_as_of(date(2030, 1, 1), None)  # no cut-off: any date


def test_the_activity_refuses_a_date_after_the_cutoff_without_retries() -> None:
    # The guard runs before the database is touched, so no engine is needed.
    activities = SurveillanceActivities(cast(Engine, None), None, CUTOFF)
    with pytest.raises(ApplicationError) as raised:
        activities.run_center(CenterRun("center_a", "2026-11-13"))
    assert raised.value.non_retryable and raised.value.type == "AfterCutoff"


def scheduled_request(cutoff: date | None) -> object:
    """The workflow argument the nightly schedule would start with."""
    action = cli.nightly(cutoff).action
    assert isinstance(action, ScheduleActionStartWorkflow)
    return action.args


def test_the_nightly_schedule_is_pinned_to_the_cutoff() -> None:
    assert scheduled_request(CUTOFF) == [SurveillanceRequest("2026-09-30")]
    assert scheduled_request(None) == [SurveillanceRequest(None)]


def test_the_cli_refuses_a_later_date_before_starting_anything(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    started: list[date | None] = []

    async def fake_run_now(as_of: date | None) -> None:
        started.append(as_of)

    monkeypatch.setattr(cli, "get_settings", lambda: Settings(data_cutoff=CUTOFF))
    monkeypatch.setattr(cli, "run_now", fake_run_now)
    monkeypatch.setattr("sys.argv", ["txlens-surveillance", "run", "--as-of", "2026-11-13"])
    assert cli.main() == 2
    assert "Refused: as of 2026-11-13 is after the data cut-off" in capsys.readouterr().out
    monkeypatch.setattr("sys.argv", ["txlens-surveillance", "run"])
    assert cli.main() == 0
    assert started == [CUTOFF]
