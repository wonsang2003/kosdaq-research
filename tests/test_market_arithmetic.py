"""KRX price-grid arithmetic.

These tests exist because a whole strategy family died to the fact they encode.
The limit-up price is not `prev_close * 1.30`; it is that value floored onto a tick
grid whose step widens with price. Once you know your entry premium, the return to
the ceiling is fully determined — there is nothing left to forecast. The orderbook
entry strategy was reported as improving its lock rate from 7.7% to 77.5% across
selection bands, and was net-negative in every one of them, because the quantity
being optimised had no bearing on the quantity being paid.
"""

from __future__ import annotations

import pytest

from src.shared.domain.entities.market import (
    DAILY_LIMIT,
    ROUND_TRIP_COST_PCT,
    MarketCode,
    Ticker,
    TradingDay,
    limit_up_price,
    tick_size,
)


@pytest.mark.parametrize(
    ("price", "expected"),
    [
        (1_999, 1), (2_000, 5), (4_999, 5), (5_000, 10),
        (19_999, 10), (20_000, 50), (49_999, 50), (50_000, 100),
        (199_999, 100), (200_000, 500), (499_999, 500), (500_000, 1_000),
    ],
)
def test_tick_ladder_boundaries(price, expected):
    """Each boundary is exclusive-below. Off-by-one here shifts every ceiling price."""
    assert tick_size(price) == expected


def test_limit_up_is_floored_not_rounded():
    """10,000 * 1.30 = 13,000 lands on the grid; 10,001 * 1.30 = 13,001.3 must floor.

    Rounding up would place the ceiling above the legal limit, which the exchange
    would reject — and in a backtest it silently manufactures a fraction of a tick
    of free profit on every limit-up trade.
    """
    assert limit_up_price(10_000) == 13_000
    assert limit_up_price(10_001) == 13_000  # 13001.3 -> floor to 10-won grid


def test_limit_up_never_exceeds_thirty_percent():
    for prev_close in (1_150, 3_333, 7_777, 24_500, 88_000, 310_000):
        assert limit_up_price(prev_close) <= prev_close * (1 + DAILY_LIMIT)


def _net_to_ceiling(entry_premium_pct: float, ceiling_premium_pct: float = 29.5) -> float:
    """Net percent from entering at `entry_premium_pct` and exiting at the ceiling.

    Round-trip friction 0.38% plus a 0.3% entry slippage multiplier, matching the
    convention used everywhere in this repository.
    """
    return (
        (1 + ceiling_premium_pct / 100) / ((1 + entry_premium_pct / 100) * 1.003) - 1
    ) * 100 - ROUND_TRIP_COST_PCT


def test_ceiling_return_is_determined_by_entry_premium_alone():
    """Entry premium explains the outcome almost perfectly — this is the refutation.

    Measured over 298 collected orderbook trades that actually reached the ceiling:

        net = +26.18 - 0.9242 * entry_premium,  R^2 = 0.9961

    An R-squared of 0.996 means there is essentially no room left for a signal.
    Whatever the orderbook says, once you know what premium you paid you already
    know what you will make. That is why raising the lock rate from 7.7% to 77.5%
    through selection bands left every band net-negative: the selection improved
    the thing that was not being paid for.

    The residual 0.4% is tick-grid variation — the ceiling premium is not exactly
    29.5% for every stock, because the +30% price is floored onto the grid.
    """
    slope = (_net_to_ceiling(20.0) - _net_to_ceiling(10.0)) / 10.0
    assert slope == pytest.approx(-0.98, abs=0.02), (
        f"slope {slope:.4f} at a fixed 29.5% ceiling; the measured cross-sectional "
        f"slope is -0.924 because the ceiling premium itself varies with tick grid"
    )
    # Monotone and steep: every extra point of entry premium costs ~1 point of net.
    premiums = [10.0, 12.0, 15.0, 20.0, 25.0]
    nets = [_net_to_ceiling(p) for p in premiums]
    assert nets == sorted(nets, reverse=True)
    assert nets[0] == pytest.approx(16.995, abs=0.01)
    assert nets[-1] == pytest.approx(2.910, abs=0.01)


def test_entry_premium_above_break_even_is_unrecoverable():
    """Past roughly +29% entry there is no ceiling exit that pays. No filter helps."""
    assert _net_to_ceiling(29.0) < 0


def test_issue_code_stays_a_string():
    """Newer KRX codes are not numeric ('0001A0'). Parsing as int corrupts joins."""
    t = Ticker(code="0001A0", market=MarketCode.KOSDAQ)
    assert t.code == "0001A0"

    with pytest.raises(ValueError):
        Ticker(code="12345", market=MarketCode.KOSDAQ)  # 5 chars


def test_index_codes_differ_per_board():
    """A single 'market return' would misattribute a KOSPI drawdown to KOSDAQ."""
    assert MarketCode.KOSPI.index_code != MarketCode.KOSDAQ.index_code


def test_trading_day_key_format():
    from datetime import date

    assert TradingDay(day=date(2010, 6, 14)).yyyymmdd == "20100614"
