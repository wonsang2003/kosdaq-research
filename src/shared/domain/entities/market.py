"""Korean equity market primitives.

Two facts about KRX drive most of this repository and are encoded here rather than
left as folklore:

  1. Prices move on a tick grid whose step widens with price. A limit-up price is
     therefore not `prev_close * 1.30` but that value floored to the grid.
  2. The daily limit is +-30%. Combined with (1), the return from any entry price
     to the ceiling is fully determined the moment you know the entry price. That
     is arithmetic, not a forecast, and it is what killed the orderbook entry
     strategy: `net ~ -0.887 * entry_premium` in every band tested.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

# (upper bound exclusive, tick size in KRW) for the KRX equity tick ladder.
_TICK_LADDER: tuple[tuple[int, int], ...] = (
    (2_000, 1),
    (5_000, 5),
    (20_000, 10),
    (50_000, 50),
    (200_000, 100),
    (500_000, 500),
)
_TICK_ABOVE = 1_000

DAILY_LIMIT = 0.30
"""KRX daily price limit, symmetric. A stock may not trade beyond +-30% of the
previous close during the regular session."""

ROUND_TRIP_COST_PCT = 0.38
"""All-in round-trip friction used throughout, in percent of notional: 0.20% sell-side
securities transaction tax (2026 rate, raised from 0.15%) plus brokerage both sides.
Every net figure in this repository has this deducted. It is deliberately not a
tunable — an earlier revision of my search code lowered its own cost assumption
to produce a tradable verdict."""


def tick_size(price: float) -> int:
    """KRW tick step applicable at `price`.

    :return: the tick size in won for the band containing `price`.
    """
    for upper, tick in _TICK_LADDER:
        if price < upper:
            return tick
    return _TICK_ABOVE


def limit_up_price(prev_close: float) -> int:
    """Exact limit-up ('상한가') price for a stock whose previous close was `prev_close`.

    The raw +30% value is floored onto the tick grid, so the realised ceiling is at
    or below `prev_close * 1.30` — never above.

    :return: the limit-up price in won.
    """
    raw = prev_close * (1 + DAILY_LIMIT)
    tick = tick_size(raw)
    return int(raw / tick + 1e-9) * tick


class MarketCode(str, Enum):
    """KRX board. The two boards need separate index series: a single 'market
    return' would misattribute a KOSPI position's drawdown to a KOSDAQ move."""

    KOSPI = "KOSPI"
    KOSDAQ = "KOSDAQ"

    @property
    def index_code(self) -> str:
        """KIS OpenAPI index identifier used to pull this board's daily index level."""
        return "0001" if self is MarketCode.KOSPI else "1001"


class Ticker(BaseModel):
    """A listed Korean equity."""

    model_config = ConfigDict(frozen=True)

    code: str = Field(min_length=6, max_length=6)
    """Six-character KRX issue code. Not always numeric — newer issues use codes
    such as '0001A0', so this must stay a string. Parsing it as an integer silently
    drops leading zeros and corrupts joins."""

    market: MarketCode

    name: str | None = None


class TradingDay(BaseModel):
    """A single KRX session.

    Sessions, not calendar days, are the unit of statistical independence here.
    Events cluster heavily — one gap-down morning produced 62 signals — so a
    per-event t-statistic overstates the sample by the clustering factor. Every
    significance figure in this repository is clustered on this entity.
    """

    model_config = ConfigDict(frozen=True)

    day: date

    @property
    def yyyymmdd(self) -> str:
        """Compact form used as the key in collected orderbook and index files."""
        return self.day.strftime("%Y%m%d")
