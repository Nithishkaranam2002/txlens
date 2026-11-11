"""A hard spending cap per batch of LLM calls, safe with many calls in flight.

Before a call, its worst-case cost is reserved with one atomic UPDATE that only succeeds while
spent + reserved + this call stays within the cap; concurrent workers therefore cannot
overshoot together. After the call the reservation is swapped for the real cost. When a
reservation fails, `BudgetExceededError` stops the batch: no retry can fix it.
"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Engine, text


class BudgetExceededError(RuntimeError):
    """The batch's spending cap would be passed by the next call."""


@dataclass(frozen=True)
class Budget:
    """The spending account of one batch (app.llm_budget; shared by all tenants)."""

    engine: Engine
    batch_id: str
    cap_usd: float

    def open(self) -> None:
        """Create the batch account; an existing batch keeps its cap and spending."""
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app.llm_budget (batch_id, cap_usd) VALUES (:b, :c) "
                    "ON CONFLICT (batch_id) DO NOTHING"
                ),
                {"b": self.batch_id, "c": self.cap_usd},
            )

    def reserve(self, amount: float) -> None:
        """Hold `amount` dollars for a call, or raise if the cap would be passed."""
        with self.engine.begin() as conn:
            held = conn.execute(
                text(
                    "UPDATE app.llm_budget SET reserved_usd = reserved_usd + :a, "
                    "updated_at = now() WHERE batch_id = :b "
                    "AND spent_usd + reserved_usd + :a <= cap_usd RETURNING batch_id"
                ),
                {"a": amount, "b": self.batch_id},
            ).first()
        if held is None:
            spent, cap = self.totals()
            raise BudgetExceededError(
                f"batch {self.batch_id}: ${spent:.4f} spent of ${cap:.2f}; "
                f"the next call may cost up to ${amount:.4f}"
            )

    def settle(self, reserved: float, actual: float) -> None:
        """Release a reservation and record what the call really cost."""
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE app.llm_budget SET "
                    "reserved_usd = greatest(reserved_usd - :r, 0), "
                    "spent_usd = spent_usd + :c, updated_at = now() WHERE batch_id = :b"
                ),
                {"r": reserved, "c": actual, "b": self.batch_id},
            )

    def totals(self) -> tuple[Decimal, Decimal]:
        """(spent, cap) in dollars."""
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT spent_usd, cap_usd FROM app.llm_budget WHERE batch_id = :b"),
                {"b": self.batch_id},
            ).one()
        return Decimal(row.spent_usd), Decimal(row.cap_usd)
