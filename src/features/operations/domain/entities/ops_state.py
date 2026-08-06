"""The state shapes the structural health checks read.

These are the inputs to `ops_alerts.build_alerts`, and they live in the domain because
the checks are statements about *what the system should look like* rather than about
where the state happens to be stored. A queue entry is stale after N days whether it
came from a JSON file, a broker, or a test fixture; binding that rule to a storage
format is how the same check ends up reimplemented per adapter and disagreeing with
itself.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict


class PendingItem(BaseModel):
    """One thing waiting in a queue: a signal awaiting submission, an order awaiting
    a fill, a reconciliation awaiting an operator."""

    model_config = ConfigDict(frozen=True)

    item_id: str
    symbol: str = ""
    queued_on: date
    kind: str = "pending"

    def age_days(self, today: date) -> int:
        return (today - self.queued_on).days


class TrackedPosition(BaseModel):
    """A held position and the day its own plan says it should be gone.

    `exit_on` is carried with the position rather than recomputed, because the check
    that matters is "did the exit rule fire", and recomputing the rule here would let
    a broken rule agree with itself.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    quantity: float
    entered_on: date
    exit_on: date

    def days_overdue(self, today: date) -> int:
        return (today - self.exit_on).days
