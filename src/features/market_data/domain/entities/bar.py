"""Market data primitives.

`Bar` is the contract every data source produces and every strategy consumes. It is
deliberately small: symbol, an aware timestamp, OHLCV, and traded value. Anything
richer belongs to a strategy and does not survive a strategy swap.

Traded *value* is carried alongside volume because it is what liquidity gates are
written against — share count means nothing without price, and a filter expressed in
shares silently changes meaning across the price range.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Bar(BaseModel):
    """One OHLCV bar."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    """Six-character KRX issue code. Kept as a string: newer codes are not numeric
    and parsing to int drops leading zeros, which corrupts every join downstream."""

    timestamp: datetime
    """Bar **open** time, timezone-aware. The convention matters and is stated here
    because a mixed convention is undetectable by inspection: half the bars appearing
    a minute late looks exactly like a market that moved a minute later."""

    interval_minutes: int = Field(gt=0)

    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)

    traded_value: float = Field(ge=0, default=0.0)
    """Notional traded in the bar, in KRW. Liquidity gates are written against this
    rather than share volume."""

    @field_validator("timestamp")
    @classmethod
    def _must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Bar.timestamp must be timezone-aware")
        return value

    @field_validator("high")
    @classmethod
    def _high_is_highest(cls, value: float, info) -> float:
        low = info.data.get("low")
        if low is not None and value < low:
            raise ValueError(f"high {value} below low {low}")
        return value

    @property
    def typical_price(self) -> float:
        """(H + L + C) / 3 — the standard volume-profile price proxy."""
        return (self.high + self.low + self.close) / 3

    @property
    def is_flat(self) -> bool:
        """No range at all. Usually a halt or an untraded minute rather than calm."""
        return self.high == self.low


class Quote(BaseModel):
    """Top-of-book snapshot, the minimum needed to decide whether a fill is possible."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timestamp: datetime

    bid: float = Field(ge=0)
    ask: float = Field(ge=0)
    """Zero is meaningful, not missing. A limit-locked book reports an ask of zero
    because there is no seller, and a naive `ask > 0` filter therefore discards the
    entire event instead of flagging it — that exact filter once cut a sample from 69
    events to 3 and produced a fabricated negative result."""

    bid_size: float = Field(ge=0, default=0.0)
    ask_size: float = Field(ge=0, default=0.0)

    @field_validator("timestamp")
    @classmethod
    def _must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Quote.timestamp must be timezone-aware")
        return value

    @property
    def is_locked(self) -> bool:
        """No resting offer. Distinct from a wide spread — nothing is for sale."""
        return self.ask == 0 or self.ask_size == 0

    @property
    def mid(self) -> float | None:
        """Midpoint, or None when the book is one-sided.

        None rather than falling back to the bid: a one-sided book has no midpoint,
        and inventing one is how an unfillable price enters a backtest.
        """
        if self.bid <= 0 or self.ask <= 0:
            return None
        return (self.bid + self.ask) / 2


class QualityReport(BaseModel):
    """Integrity findings for one symbol's stored bars.

    Data quality is checked before any statistic is computed, not after a result
    looks wrong. A duplicated row set once fabricated a +1.5%/day edge in this
    project's history — the defect was in the loader, and every downstream test was
    correct about the data it was given.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    rows: int
    duplicate_timestamps: int
    out_of_session_rows: int
    non_positive_prices: int
    estimated_missing_minutes: int
    warnings: tuple[str, ...] = ()

    @property
    def is_clean(self) -> bool:
        return not self.warnings and self.duplicate_timestamps == 0
