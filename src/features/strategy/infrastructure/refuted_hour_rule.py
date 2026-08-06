"""The rule this project selected on a training fold and then refuted.

Shipping a *failure* as the default implementation is deliberate. The strategies this
project confirmed are running on the author's own capital and are not in this
repository — see `docs/DISCLOSURE.md`. What is here is the one rule whose evaluation is
complete enough to publish in full, because publishing a refutation gives nothing away
and is the only trading claim in this repository a reader can re-run end to end.

**The rule.** Rank the cross-section by turnover relative to each name's own recent
norm, and trade only inside one chosen hour of the session. Hour 13 was chosen because
it was the best of twelve configurations on the training fold, at +0.116% per trade
after cost.

**Why it is marked REFUTED.** `data/audit/hour_scan_12_configs.json` is my own
scanner's output, unmodified. It also contains the test-fold column, computed in the
same run, which I did not read before writing `hour == 13` into the strategy:

* hour 13 returned **-0.854% per trade** out of sample and ranked **8th of 12**;
* **all twelve** configurations lost money out of sample — there was no hour to pick,
  only a choice of which negative number to write into the code;
* train and test correlate at **+0.296** across configurations, which makes the
  selection structurally near-random rather than unlucky.

Full write-up: `docs/postmortems/04-train-selection-hour-scan.md`.

**Why the implementation is this plain.** Every knob that could be added here — a
liquidity filter, a finer time bucket, a second ranking term — increases the number of
configurations searched, which raises the significance bar that the existing result
already fails to clear. Making this file cleverer would be a fresh instance of the
mistake it exists to document.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from src.features.market_data.domain.entities.bar import Bar
from src.features.strategy.domain.entities.signal import Side, Signal, StrategySpec
from src.features.strategy.domain.repositories.strategy import Strategy, StrategyError
from src.shared.domain.clock import MARKET_TZ

SELECTED_HOUR = 13
"""The train-argmax. Read off `data/audit/hour_scan_12_configs.json`, not tuned here —
`tests/test_strategies.py` re-derives it from that file so the two cannot drift."""

CONFIGURATIONS_SCANNED = 12
TRAIN_RETURN_PCT = 0.116
TEST_RETURN_PCT = -0.854
TEST_RANK_OF_TRAIN_ARGMAX = 8
TRAIN_TEST_PEARSON = 0.296

EVIDENCE = "docs/postmortems/04-train-selection-hour-scan.md"

DEFAULT_LOOKBACK_BARS = 20
"""Bars of history each name is compared against itself over. Fixed rather than
searched: a lookback chosen by trying several is another selection over k, and the
whole finding here is what selection over k does."""


def assert_usable_cross_section(
    as_of: datetime, bars_by_symbol: Mapping[str, Sequence[Bar]]
) -> None:
    """Reject a cross-section that would produce a confident, wrong answer.

    Three refusals, each for a defect that is invisible in the output it corrupts:

    * **A naive `as_of`.** Comparing it against aware bar timestamps raises deep inside
      the ranking loop, or worse, is silently assumed to be exchange-local by a caller
      that normalises first. The hour gate below is exchange-local, so a naive instant
      is not merely awkward, it selects the wrong hour.
    * **A bar stamped after `as_of`.** The port promises the caller has already
      truncated. If that promise is broken the strategy cannot detect it later — it
      just produces a better result. Look-ahead is the one defect that never announces
      itself, so it is checked at the boundary rather than trusted.
    * **Timestamps that do not strictly increase.** A duplicated row set in the loader
      once fabricated a +1.5%/day edge in this project's history, and every downstream
      test was correct about the data it was given. Duplicates change a turnover
      average without changing anything a reader would look at.

    :raise StrategyError: on any of the above. Never returns a filtered cross-section:
        quietly dropping the offending bars would let a broken loader keep running.
    """
    if as_of.tzinfo is None:
        raise StrategyError(
            "as_of must be timezone-aware; a naive instant would be gated against the "
            "wrong exchange-local hour"
        )
    for symbol, bars in bars_by_symbol.items():
        previous: datetime | None = None
        for bar in bars:
            if bar.timestamp > as_of:
                raise StrategyError(
                    f"{symbol}: bar at {bar.timestamp.isoformat()} is after as_of "
                    f"{as_of.isoformat()} — upstream truncation failed, this is look-ahead"
                )
            if previous is not None and bar.timestamp <= previous:
                raise StrategyError(
                    f"{symbol}: bars are not strictly increasing at "
                    f"{bar.timestamp.isoformat()} — duplicated or unsorted rows"
                )
            previous = bar.timestamp


class RefutedHourRule(Strategy):
    """Trade one hour of the session, ranked by relative turnover. Documented to lose.

    The runtime prints `describe()` before running, so a reviewer executing this is
    told by the program — not only by a document — that the losses that follow are the
    expected outcome.
    """

    def __init__(
        self,
        hour: int = SELECTED_HOUR,
        lookback_bars: int = DEFAULT_LOOKBACK_BARS,
    ) -> None:
        """
        :param hour: exchange-local hour to trade. Parameterised only so a reader can
            reproduce the other eleven configurations in the audit file; the shipped
            default is the one that was actually written into the strategy.
        :param lookback_bars: bars each name's latest turnover is compared against.
        :raise StrategyError: on a nonsensical configuration, at construction time
            rather than at the first evaluation, so a misconfigured run cannot look
            like a quiet day.
        """
        if not 0 <= hour <= 23:
            raise StrategyError(f"hour must be 0..23, got {hour}")
        if lookback_bars < 1:
            raise StrategyError(f"lookback_bars must be at least 1, got {lookback_bars}")
        self.hour = hour
        self.lookback_bars = lookback_bars

    @property
    def spec(self) -> StrategySpec:
        return StrategySpec(
            key="refuted_hour",
            name=f"Hour-{self.hour} relative-turnover rank",
            verdict="REFUTED",
            summary=(
                f"train-selection under multiplicity: best of {CONFIGURATIONS_SCANNED} "
                f"on train at {TRAIN_RETURN_PCT:+.3f}%/trade, "
                f"{TEST_RANK_OF_TRAIN_ARGMAX}th of {CONFIGURATIONS_SCANNED} on test at "
                f"{TEST_RETURN_PCT:+.3f}%/trade; all {CONFIGURATIONS_SCANNED} "
                f"configurations lost money out of sample, "
                f"Pearson(train, test) = {TRAIN_TEST_PEARSON:+.3f}"
            ),
            evidence=EVIDENCE,
        )

    def evaluate(
        self,
        as_of: datetime,
        bars_by_symbol: dict[str, list[Bar]],
        max_signals: int,
    ) -> list[Signal]:
        """Rank the cross-section, but only during the chosen hour.

        Outside the hour the result is an empty list, which is a result and not an
        error: the rule had no opportunity because the rule declined to look. That is
        the distinction the port insists on, and it is why failures above raise instead.
        """
        assert_usable_cross_section(as_of, bars_by_symbol)
        if max_signals < 0:
            raise StrategyError(f"max_signals must not be negative, got {max_signals}")

        if as_of.astimezone(MARKET_TZ).hour != self.hour:
            return []

        scored: list[tuple[str, float]] = []
        for symbol in sorted(bars_by_symbol):
            ratio = self._relative_turnover(bars_by_symbol[symbol])
            if ratio is not None:
                scored.append((symbol, ratio))

        # Symbol as the tie-break, so an identical cross-section presented in a
        # different dict order produces an identical ranking. Without it the ranks
        # depend on insertion order, and a backtest and a live run iterate different
        # dicts.
        scored.sort(key=lambda item: (-item[1], item[0]))

        return [
            Signal(
                symbol=symbol,
                side=Side.BUY,
                timestamp=as_of,
                rank=position,
                reason=(
                    f"hour {self.hour} KST; turnover {ratio:.2f}x its own trailing "
                    f"{self.lookback_bars}-bar mean; REFUTED configuration — "
                    f"{TEST_RETURN_PCT:+.3f}%/trade out of sample ({EVIDENCE})"
                ),
            )
            for position, (symbol, ratio) in enumerate(scored[:max_signals], start=1)
        ]

    def _relative_turnover(self, bars: Sequence[Bar]) -> float | None:
        """Latest bar's traded value against this name's own trailing mean.

        Relative rather than absolute on purpose: an absolute turnover ranking is a
        market-capitalisation ranking wearing a disguise, and returns the same handful
        of large names every hour of every day.

        :return: the ratio, or None when this name cannot be ranked — fewer than
            `lookback_bars + 1` bars, or a trailing mean of zero. None rather than a
            number, because a ratio against zero turnover is undefined, not enormous;
            substituting infinity would sort the least-traded names to rank 1, which is
            the exact inversion of what the rule intends.
        """
        needed = self.lookback_bars + 1
        if len(bars) < needed:
            return None
        window = bars[-needed:-1]
        baseline = sum(bar.traded_value for bar in window) / len(window)
        if baseline <= 0:
            return None
        return bars[-1].traded_value / baseline
