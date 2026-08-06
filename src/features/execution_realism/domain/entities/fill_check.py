"""Fill feasibility outcome — why a price is or is not obtainable."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict

from src.shared.domain.errors import DomainError


class Unfillable(DomainError):
    """A backtest attempted to transact at a price that cannot be obtained."""


class FillObstruction(str, Enum):
    """Why a price is unobtainable. Each corresponds to a strategy that died to it."""

    LIMIT_LOCKED = "limit_locked"
    """Price is pinned at the daily limit. The definitive case: a strategy with a
    Newey-West t of 48.0 across thirteen consecutive profitable years filled zero of
    twenty live orders and took zero allocation across thirty-two closing auctions."""

    NO_OPPOSING_SIZE = "no_opposing_size"
    """The book carries no size on the side that must be crossed. Note that a locked
    book reports an ask of zero rather than an absent ask, so a naive `ask > 0` filter
    silently discards the entire event instead of flagging it — that exact filter once
    reduced a sample from 69 events to 3 and produced a fabricated negative result."""

    SIGNAL_AFTER_PRICE = "signal_after_price"
    """The signal is confirmed at or after the timestamp of the price being transacted.
    A futures fade at t = 3.71 entered at the opening print on a signal that only exists
    once that print is known; re-run on the first executable leg it fell to t = 1.34."""


class FillCheck(BaseModel):
    """Result of a feasibility check on one intended transaction."""

    model_config = ConfigDict(frozen=True)

    feasible: bool
    obstruction: FillObstruction | None = None
    detail: str = ""

    def raise_if_infeasible(self) -> None:
        """
        :raise Unfillable: if this transaction cannot be obtained. Callers must not
            coerce an infeasible check into a skipped observation — a dropped row and a
            refused row are different populations, and conflating them is how a fill
            problem disguises itself as a smaller sample.
        """
        if not self.feasible:
            raise Unfillable(f"{self.obstruction.value if self.obstruction else '?'}: {self.detail}")
