"""Fill feasibility — is the price the backtest assumed a price you can obtain?

This is the check that killed more strategies here than every statistical test combined,
and no statistical test can substitute for it. The failure is not that a signal fails to
generalise; it is that the signal generalises perfectly to a price that does not exist
for this participant.

The single most replicated finding across four markets, confirmed independently six
times: **fillability and edge are mutually exclusive.** Measured directly against real
order books, the orders that could not be filled had an ideal net roughly three times
better than the orders that did. Selection into the filled set is adverse by
construction.

Three conditions are checked, because three different strategies died to one each:

  `LIMIT_LOCKED`   the close is pinned at the daily limit — the buy queue is enormous
                   and pro-rata allocation to a small order rounds to nothing
  `NO_OPPOSING_SIZE` the book has no size on the side you must cross
  `SIGNAL_AFTER_PRICE` the signal confirms at or after the instant of the price it
                   claims to transact at, so the entry is a ghost leg

A backtest that transacts under any of these has not measured a strategy; it has
measured an accounting identity.
"""

from __future__ import annotations


from src.features.execution_realism.domain.entities.fill_check import (
    FillCheck,
    FillObstruction,
    Unfillable,
)
from src.shared.domain.entities.market import limit_up_price


def check_buy(
    price: float,
    prev_close: float,
    opposing_size: float | None,
    signal_time: float | None = None,
    price_time: float | None = None,
) -> FillCheck:
    """Whether a buy at `price` is obtainable.

    :param price: the price the backtest intends to pay.
    :param prev_close: previous session close, used to locate the daily limit.
    :param opposing_size: resting ask size. `None` means unknown, which is treated as
        infeasible — an unknown book is not an empty obstruction. Zero on a locked book
        means locked, not merely thin.
    :param signal_time: epoch seconds at which the signal is confirmed, if known.
    :param price_time: epoch seconds of the price being transacted, if known.
    :return: a `FillCheck`. Feasibility is judged conservatively: any condition that
        cannot be evaluated counts against feasibility.
    """
    if signal_time is not None and price_time is not None and signal_time >= price_time:
        return FillCheck(
            feasible=False,
            obstruction=FillObstruction.SIGNAL_AFTER_PRICE,
            detail=(f"signal confirms at {signal_time} but the price is stamped "
                    f"{price_time}; entry at that price is a ghost leg"),
        )

    ceiling = limit_up_price(prev_close)
    if price >= ceiling:
        return FillCheck(
            feasible=False,
            obstruction=FillObstruction.LIMIT_LOCKED,
            detail=(f"{price:,.0f} is at or above the limit-up price {ceiling:,.0f}; "
                    f"allocation to a retail-sized order is not obtainable"),
        )

    if opposing_size is None or opposing_size <= 0:
        return FillCheck(
            feasible=False,
            obstruction=FillObstruction.NO_OPPOSING_SIZE,
            detail=("no resting ask size" if opposing_size == 0 else
                    "ask size unknown; an unevaluable book counts against feasibility"),
        )

    return FillCheck(feasible=True, detail="opposing size present below the daily limit")


def participation_rate(order_value: float, traded_value: float) -> float:
    """Order size as a fraction of the volume it must actually compete with.

    :param order_value: intended notional.
    :param traded_value: notional traded **in the window the order participates in** —
        not the daily figure. Using a daily denominator against an opening-auction entry
        overstated capacity by roughly sixty-nine times in one measured case, and
        enforcing a 10% participation cap afterwards cut realised value per trade by 39%.
    :return: the participation fraction.
    :raise Unfillable: if `traded_value` is not positive; a zero denominator here means
        there was no volume to participate in, which is a fill problem, not a division
        problem.
    """
    if traded_value <= 0:
        raise Unfillable("no traded value in the participation window")
    return order_value / traded_value
