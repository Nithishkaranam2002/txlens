"""The data cut-off: surveillance never runs as of a day after the data ends.

A synthetic extract ends on one date. Running "as of" a later day would see no new tests and
open "missed test" flags that the data cannot explain, and the worklist would label every
real flag as older data. With a cut-off set (`DATA_CUTOFF`), a run without a date runs as of
the cut-off and a later date is refused.
"""

from datetime import date


class AfterCutoffError(ValueError):
    """An as-of date after the data cut-off."""

    def __init__(self, as_of: date, cutoff: date) -> None:
        super().__init__(
            f"as of {as_of.isoformat()} is after the data cut-off {cutoff.isoformat()}: "
            "surveillance would flag tests the data cannot contain"
        )


def check_as_of(as_of: date, cutoff: date | None) -> None:
    """Refuse an as-of date after the cut-off (no cut-off: any date)."""
    if cutoff is not None and as_of > cutoff:
        raise AfterCutoffError(as_of, cutoff)


def pinned_as_of(requested: date | None, cutoff: date | None) -> date | None:
    """The date to run: the requested one (checked), else the cut-off. None means today."""
    if requested is None:
        return cutoff
    check_as_of(requested, cutoff)
    return requested
