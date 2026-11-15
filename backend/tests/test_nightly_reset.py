"""The cloud demo's nightly reset: the systemd timer's schedule, and that a run resets only
while sharing mode is on (dry runs of infra/azure/remote/reset-timer.sh, nothing changed)."""

import subprocess
from pathlib import Path

import pytest

REMOTE = Path(__file__).parents[2] / "infra" / "azure" / "remote"
SCRIPT = REMOTE / "reset-timer.sh"


def unit(name: str) -> dict[str, str]:
    """`key=value` lines of a unit file (no repeated keys in ours)."""
    lines = (REMOTE / name).read_text().splitlines()
    return dict(line.split("=", 1) for line in lines if "=" in line and not line.startswith("#"))


def test_timer_fires_at_three_central_and_does_not_catch_up_at_boot() -> None:
    timer = unit("txlens-reset.timer")
    assert timer["OnCalendar"] == "*-*-* 03:00:00 America/Chicago"
    assert timer["Persistent"] == "false"
    assert timer["Unit"] == "txlens-reset.service"


def test_service_runs_the_reset_as_the_admin_user_not_root() -> None:
    service = unit("txlens-reset.service")
    assert service["User"] == "@ADMIN@"
    assert service["Type"] == "oneshot"
    assert service["ExecStart"].endswith("reset-timer.sh run @DOMAIN@")


def dry_run(tmp_path: Path, state: str | None, today: str = "2026-11-20") -> tuple[str, str]:
    """(output, log) of a dry run with this sharing state, on this day (US Central)."""
    state_file, log, snapshot = tmp_path / "until", tmp_path / "reset.log", tmp_path / "m.json"
    snapshot.write_text("{}")
    if state is not None:
        state_file.write_text(state)
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "TXLENS_RESET_STATE": str(state_file),
           "TXLENS_RESET_LOG": str(log), "TXLENS_RESET_SNAPSHOT": str(snapshot),
           "TXLENS_TODAY": today}  # fmt: skip
    done = subprocess.run(["bash", str(SCRIPT), "run", "demo.example", "--dry-run"], env=env,
                          capture_output=True, text=True, check=True)  # fmt: skip
    return done.stdout, log.read_text()


@pytest.mark.parametrize(
    ("state", "resets"),
    [("2026-12-04", True), ("2026-11-20", True), ("2026-11-19", False), (None, False),
     ("not-a-date", False)],
    ids=["sharing", "last-day", "sharing-over", "sharing-off", "garbage"],
)  # fmt: skip
def test_dry_run_resets_only_while_sharing(tmp_path: Path, state: str | None, resets: bool) -> None:
    out, log = dry_run(tmp_path, state)
    expected = "would reset demo.example now" if resets else "the reset would be skipped"
    assert expected in out
    [line] = log.splitlines()  # one log line per run, starting with a UTC timestamp
    assert expected in line and line[:4].isdigit() and line.split(" ", 1)[0].endswith("Z")
