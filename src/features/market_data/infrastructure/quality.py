"""Integrity checks that run before a statistic is computed, not after one looks wrong.

The incident this module answers to is recorded in `QualityReport`'s own docstring: a
duplicated row set fabricated a +1.5%/day edge, and every downstream test was correct
about the data it was given. Nothing in a backtest can detect a duplicated bar — the
arithmetic is valid, the Sharpe is real, the sample is a lie. The only place to catch
it is at the boundary, by counting.

Everything here reports; nothing here repairs. A checker that silently drops
duplicates hides the loader bug that produced them, and the loader bug is the thing
worth fixing.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from src.features.market_data.domain.entities.bar import Bar, QualityReport
from src.shared.domain import clock

DEFAULT_SESSION_MINUTES = 391
"""Minutes in one KRX regular session, 09:00–15:30 inclusive of both ends.

A constant rather than a literal at the call site. `391` appeared inline in the
system this replaces, and when the venue's closing auction moved, the number was
found in four places and changed in two — after which every panel reported a steady
missing-bar count that nobody could source. Anything derived from it is computed by
`expected_bars_per_session`, and the value is overridable per call because a
pre-market or after-hours panel has a different denominator entirely.
"""


def expected_bars_per_session(
    interval_minutes: int, session_minutes: int = DEFAULT_SESSION_MINUTES
) -> int:
    """How many bars a complete session should hold at this resolution.

    :param session_minutes: length of the session being sampled. Override it for a
        panel that is not the regular continuous session.
    :raise ValueError: on a non-positive interval, rather than dividing by it.
    """
    if interval_minutes <= 0:
        raise ValueError(f"interval_minutes must be positive, got {interval_minutes}")
    if session_minutes <= 0:
        raise ValueError(f"session_minutes must be positive, got {session_minutes}")
    return max(1, session_minutes // interval_minutes)


def analyze_bars(
    symbol: str,
    bars: Sequence[Bar],
    expected_per_day: int | None = None,
    session_minutes: int = DEFAULT_SESSION_MINUTES,
) -> QualityReport:
    """Count what is wrong with one symbol's bars, without changing them.

    :param expected_per_day: bars a complete session should hold. `None` derives it
        from the interval the bars actually carry, which is right for a homogeneous
        panel and wrong for a mixed one — so a mixed panel is flagged in `warnings`
        instead of being silently averaged.
    :param session_minutes: passed through to `expected_bars_per_session`.
    :return: a report. Counts are returned even when they are zero; an empty
        `warnings` tuple is the positive statement that the checks ran and found
        nothing, which a caller cannot infer from a report that was never produced.
    """
    warnings: list[str] = []
    rows = len(bars)

    if rows == 0:
        # Not an error and not silently fine: a symbol expected in the universe that
        # stores no rows at all is usually a collection failure, and the caller is the
        # only one who knows whether it was expected.
        return QualityReport(
            symbol=symbol,
            rows=0,
            duplicate_timestamps=0,
            out_of_session_rows=0,
            non_positive_prices=0,
            estimated_missing_minutes=0,
            warnings=("no rows to analyse",),
        )

    foreign = sum(1 for bar in bars if bar.symbol != symbol)
    if foreign:
        warnings.append(
            f"{foreign} rows carry a different symbol than {symbol} — check the loader's join"
        )

    stamps = [bar.timestamp for bar in bars]
    duplicates = rows - len({stamp.astimezone(clock.MARKET_TZ) for stamp in stamps})
    if duplicates:
        warnings.append(
            f"{duplicates} duplicate timestamps — a repeated row inflates any "
            "per-bar statistic without making the arithmetic look wrong"
        )

    out_of_session = sum(1 for stamp in stamps if not clock.is_within_session(stamp))
    if out_of_session:
        warnings.append(
            f"{out_of_session} rows fall outside the regular session "
            "(09:00–15:30 KST, weekdays)"
        )

    # `Bar` validates prices as strictly positive, so a validated bar cannot fail this.
    # It is still counted, because `Bar.model_construct` skips validation and is the
    # obvious shortcut for a loader turning a million parquet rows into objects.
    non_positive = sum(
        1
        for bar in bars
        if min(bar.open, bar.high, bar.low, bar.close) <= 0
    )
    if non_positive:
        warnings.append(
            f"{non_positive} rows carry a non-positive price — these bypassed "
            "Bar validation and cannot have come from a validated path"
        )

    inverted = sum(1 for bar in bars if bar.high < bar.low)
    if inverted:
        warnings.append(f"{inverted} rows have high below low")

    intervals = Counter(bar.interval_minutes for bar in bars)
    if len(intervals) > 1:
        warnings.append(
            f"mixed bar intervals {sorted(intervals)} — the missing-minute estimate "
            "assumes one resolution and is not meaningful here"
        )

    if expected_per_day is None:
        expected_per_day = expected_bars_per_session(
            intervals.most_common(1)[0][0], session_minutes
        )
    if expected_per_day <= 0:
        raise ValueError(f"expected_per_day must be positive, got {expected_per_day}")

    missing, sessions = _missing_minutes(stamps, expected_per_day)
    if missing:
        warnings.append(
            f"about {missing} bars missing across {sessions} observed sessions "
            f"(expected {expected_per_day} per session)"
        )

    return QualityReport(
        symbol=symbol,
        rows=rows,
        duplicate_timestamps=duplicates,
        out_of_session_rows=out_of_session,
        non_positive_prices=non_positive,
        estimated_missing_minutes=missing,
        warnings=tuple(warnings),
    )


def _missing_minutes(stamps: Sequence, expected_per_day: int) -> tuple[int, int]:
    """Shortfall against `expected_per_day`, counted only on days that have any rows.

    A day with zero rows is invisible here and that is intentional. Without a holiday
    calendar, "no bars on this date" and "the exchange was closed on this date" are
    the same observation, and guessing produces a missing-bar count that grows with
    every public holiday — a number that is wrong in a direction nobody investigates
    because it always looks plausible. `clock.is_within_session` documents the same
    refusal. A whole-day hole is the trading-calendar port's to find.

    :return: (estimated missing bars, number of days that had at least one row).
    """
    per_day: dict[str, set] = {}
    for stamp in stamps:
        per_day.setdefault(clock.day_key(stamp), set()).add(
            stamp.astimezone(clock.MARKET_TZ)
        )
    missing = sum(max(0, expected_per_day - len(seen)) for seen in per_day.values())
    return missing, len(per_day)
