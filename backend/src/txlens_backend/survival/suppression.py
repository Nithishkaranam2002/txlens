"""Small-cell suppression of survival output, for every role (the output is exportable).

- Counts of 1-10 (patients, events, numbers at risk, events per interval) are shown as "<11".
- A curve shows a step only once at least 11 events have built up since the last step shown,
  so no single drop reveals fewer than 11 patients; the value shown is the true estimate at
  that time. The curve stops before the number at risk falls below 11.
- A group with fewer than 11 events gets no curve at all.
- Values read "at 1 year" come off the displayed curve, so they reveal nothing it does not.

Exports are built from the already-suppressed result, never from raw numbers.
"""

from dataclasses import dataclass

import numpy as np
from pydantic import BaseModel

from txlens_backend.deid import suppress
from txlens_backend.survival.km import Curve

Count = int | str


class Point(BaseModel):
    """One displayed step: days since transplant, estimate and 95% CI."""

    day: float
    estimate: float
    lower: float | None
    upper: float | None


class ShownCurve(BaseModel):
    """A curve as displayed: pooled steps, or none with the reason."""

    group: str
    n: Count
    events: Count
    points: list[Point]
    hidden_reason: str | None


@dataclass(frozen=True)
class Rule:
    """The suppression threshold and where the clock starts (the landmark, in days)."""

    threshold: int
    offset_days: int


def number(value: float) -> float | None:
    """A finite float, or None (an undefined interval end)."""
    return float(value) if np.isfinite(value) else None


def pooled_points(
    curve: Curve, start: float, rule: Rule, *, complement: bool = False
) -> list[Point]:
    """Steps where >= threshold events have built up, while >= threshold are at risk.
    `complement` shows 1 - estimate (1 - KM), with the interval flipped."""
    points = [Point(day=rule.offset_days, estimate=start, lower=None, upper=None)]
    pending = 0
    for j in range(len(curve.time)):
        if curve.n_risk[j] < rule.threshold:
            break
        pending += int(curve.n_event[j])
        if pending >= rule.threshold:
            est, low, up = curve.estimate[j], curve.lower[j], curve.upper[j]
            if complement:
                est, low, up = 1 - est, 1 - up, 1 - low
            points.append(
                Point(
                    day=rule.offset_days + float(curve.time[j]),
                    estimate=float(est),
                    lower=number(low),
                    upper=number(up),
                )
            )
            pending = 0
    return points


def shown_curve(
    curve: Curve, n: int, start: float, rule: Rule, *, complement: bool = False
) -> ShownCurve:
    """The displayed version of one group's curve."""
    hidden = curve.events < rule.threshold
    return ShownCurve(
        group=curve.group,
        n=suppress(n, rule.threshold),
        events=suppress(curve.events, rule.threshold),
        points=[] if hidden else pooled_points(curve, start, rule, complement=complement),
        hidden_reason=f"fewer than {rule.threshold} events" if hidden else None,
    )


def read_off(points: list[Point], day: float) -> float | None:
    """The displayed curve's value at `day` (None past its last step or before its start)."""
    if not points or not points[0].day <= day <= points[-1].day:
        return None
    return [p for p in points if p.day <= day][-1].estimate
