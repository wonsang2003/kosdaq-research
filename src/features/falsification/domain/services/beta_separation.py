"""Beta separation — telling alpha apart from market drift.

This is the correction that saved a strategy rather than killing one, which is why
it is here. A long book showed a negative raw forward return and looked like a
failure. Over the same holding periods the market was down several times as far. Subtracting the market exposure turns the same trades into a positive
excess. Judging on the raw number alone would have retired a working strategy
during a crash, and a rule that fires hardest exactly when markets fall is not a
risk control — it is a mechanism for selling the bottom.

The reverse direction matters equally: a defensive low-volatility basket reporting
'+12.9%' was holding beta, not skill. Same arithmetic, opposite conclusion.

Two conventions here are deliberate and were argued for:

  * Beta is set *below* the measured value. A lower beta credits less of the loss to
    the market, so it makes the "blame the market" defence harder to invoke, not
    easier. Conservatism here means being harsh on your own strategy.
  * A missing index observation must not be treated as a zero return. Zero would
    silently make excess equal raw and disable the whole mechanism at the moment it
    is most needed. Absence propagates as None and the caller must branch on it.
"""

from __future__ import annotations

from collections.abc import Mapping

KILL_BETA = 0.5
"""Beta used when deciding whether to stop a strategy.

Set deliberately below the measured exposure of the basket it governs. Crediting less
of a drawdown to the market raises the bar a strategy must clear to be spared. The
display layer must use this same value: if the dashboard and the kill rule disagree
about beta, the screen and the decision tell different stories.
"""

SIGN_FLIP_BETA = 0.040
"""The beta at which the excess changes sign, for the basket this was measured on.
The value is published because it is what makes the conclusion beta-independent;
the returns it was computed from are not.

Recorded because it settles the usual objection. Arguing about the right beta is
irrelevant to the conclusion when the sign holds for any beta above 0.04 — that is the
whole point of reporting the flip point rather than defending one estimate. The sign is
robust even though the magnitude is not.
"""

MissingIndex = None
"""Sentinel alias for readability at call sites: absence, never zero."""


def index_return(
    index_map: Mapping[str, Mapping[str, float]] | None,
    market: str | None,
    start_day: str | None,
    end_day: str | None,
) -> float | None:
    """Index return in percent over a holding period.

    Falls back to the previous available session when a date is not itself a
    trading day, which is required because holding periods routinely start or end
    on a weekend or holiday boundary.

    :param index_map: {"KOSDAQ": {"YYYYMMDD": level}, "KOSPI": {...}}. None or empty
        means the index could not be resolved.
    :param market: board name. Anything starting with "KOSPI" (case-insensitive)
        selects KOSPI; everything else, including None, selects KOSDAQ.
    :param start_day: entry date, "YYYY-MM-DD" or "YYYYMMDD".
    :param end_day: exit date, same formats.
    :return: percent return, or None if either endpoint cannot be resolved. None
        means "not measurable" and must never be coerced to 0.0 by the caller.
    """
    if not index_map:
        return None
    key = "KOSPI" if (market or "KOSDAQ").upper().startswith("KOSPI") else "KOSDAQ"
    levels = index_map.get(key)
    if not levels:
        return None

    ordered = sorted(levels)

    def level_at(day: str | None) -> float | None:
        day = (day or "").replace("-", "")
        if not day:
            return None
        earlier = [k for k in ordered if k <= day]
        return levels[earlier[-1]] if earlier else None

    start, end = level_at(start_day), level_at(end_day)
    if not start or not end:
        return None
    return (end / start - 1) * 100


def excess_return(net_pct: float, market_return_pct: float, beta: float = KILL_BETA) -> float:
    """Return with the market component removed.

    :param net_pct: realised net return of the position, in percent.
    :param market_return_pct: index return over the same holding period, in percent.
    :param beta: exposure to the index. Defaults to the conservative kill beta.
    :return: net - beta * market, in percent.
    """
    return net_pct - beta * market_return_pct


def should_stop(
    raw_avg_pct: float,
    excess_avg_pct: float | None,
    threshold_pct: float,
    missing_ratio: float = 0.0,
    missing_tolerance: float = 0.3,
) -> tuple[bool, str]:
    """Whether a strategy's stop condition is met.

    The rule that this encodes: a stop requires **both** the raw and the excess
    return to breach. Breaching on raw alone is a market event, not a strategy
    failure, and is reported without stopping.

    :param raw_avg_pct: average raw net across recent trades.
    :param excess_avg_pct: average beta-adjusted excess, or None if unmeasurable.
    :param threshold_pct: the stop threshold, negative.
    :param missing_ratio: fraction of trades whose index return could not be
        resolved.
    :param missing_tolerance: above this fraction the excess is not trustworthy.
    :return: (stop, reason). When too much index data is missing the answer is to
        stop — an unmeasurable excess must not become a free pass.
    """
    if raw_avg_pct > threshold_pct:
        return False, "raw above threshold"
    if excess_avg_pct is None or missing_ratio > missing_tolerance:
        return True, (
            f"index missing for {missing_ratio:.0%} of trades — excess not "
            f"measurable; holding the stop rather than assuming market blame"
        )
    if excess_avg_pct <= threshold_pct:
        return True, (
            f"raw {raw_avg_pct:+.2f}% and excess(beta={KILL_BETA}) "
            f"{excess_avg_pct:+.2f}% both at or below {threshold_pct:+.2f}%"
        )
    return False, (
        f"raw {raw_avg_pct:+.2f}% breached but excess(beta={KILL_BETA}) "
        f"{excess_avg_pct:+.2f}% did not — attributable to the market, stop withheld"
    )

