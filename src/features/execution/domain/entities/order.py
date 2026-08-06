"""Order lifecycle types.

Ported in shape from the one correctly-abstracted piece of the scanner codebase. Two
properties of that original are kept deliberately:

**Partial fills are first class.** An order is not a boolean. Most of the interesting
failures in this project's history live in the gap between "submitted" and "filled" —
0 of 20 orders allocated, 14 of 14 unfilled, a fill rate that fell from 86% to 12%
under selection. A type that cannot express partial fill cannot express those.

**Status transitions are explicit.** `OPEN → PARTIALLY_FILLED → FILLED` with terminal
states that reject further fills, so a double-fill is a raised error rather than a
silently doubled position.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.features.strategy.domain.entities.signal import Side


class OrderState(str, Enum):
    """Where an order is in its life."""

    PENDING = "PENDING"
    """Constructed, not yet accepted. Exists so that a crash between construction and
    submission is distinguishable from an order the venue never saw."""

    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"

    REJECTED = "REJECTED"
    """Refused before reaching the venue — insufficient cash, a naked short, or the
    kill switch. Carries a reason; a rejection without one is unactionable at 3am."""

    @property
    def is_terminal(self) -> bool:
        return self in {OrderState.FILLED, OrderState.CANCELLED, OrderState.REJECTED}

    @property
    def is_working(self) -> bool:
        """Still able to receive fills — the set that must be restored after a restart."""
        return self in {OrderState.OPEN, OrderState.PARTIALLY_FILLED}


class Fill(BaseModel):
    """One execution against an order. Immutable; a correction is a new record."""

    model_config = ConfigDict(frozen=True)

    order_id: str
    symbol: str
    side: Side
    quantity: float = Field(gt=0)
    price: float = Field(gt=0)
    timestamp: datetime

    @field_validator("timestamp")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Fill.timestamp must be timezone-aware")
        return value

    @property
    def notional(self) -> float:
        return self.quantity * self.price


class Order(BaseModel):
    """An instruction and everything that has happened to it."""

    model_config = ConfigDict(frozen=True)

    order_id: str
    symbol: str
    side: Side
    quantity: float = Field(gt=0)
    limit_price: float | None = None
    """None means market order. Market orders are permitted but every strategy in this
    repository's graveyard that relied on one died to slippage, so the default in the
    application layer is a marketable limit."""

    state: OrderState = OrderState.PENDING
    filled_quantity: float = Field(ge=0, default=0.0)
    average_fill_price: float = Field(ge=0, default=0.0)

    created_at: datetime
    reason: str = ""
    """Why it was created, or why it was rejected. Written for the journal reader."""

    idempotency_key: str = ""
    """Caller-supplied token, conventionally `"<session-day>|<symbol>|<intent>"`.

    The composite key *is* the deduplication mechanism: a job that runs twice
    produces the same key and the second submission is refused. This is what makes
    the scheduler safe to re-fire, which in turn is what makes a restart safe."""

    @field_validator("created_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Order.created_at must be timezone-aware")
        return value

    @property
    def remaining_quantity(self) -> float:
        return max(0.0, self.quantity - self.filled_quantity)

    @property
    def is_complete(self) -> bool:
        return self.state.is_terminal


class Position(BaseModel):
    """Net holding in one instrument, reconstructed from fills.

    Derived rather than stored independently, so it cannot disagree with the journal.
    The imitated system kept positions in a dict that a restart emptied while the
    journal it could have been rebuilt from sat unread on disk.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    quantity: float = 0.0
    average_price: float = Field(ge=0, default=0.0)

    @property
    def is_flat(self) -> bool:
        return self.quantity == 0

    @property
    def notional(self) -> float:
        return abs(self.quantity) * self.average_price
