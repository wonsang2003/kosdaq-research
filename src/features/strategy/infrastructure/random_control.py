"""The seeded random control — the bar a candidate strategy has to clear.

This is shipped as a first-class implementation rather than as a test fixture because
the comparison is part of the method, not part of the testing. `METHODS.md` lists
"random null" among the harness gates for exactly this reason: a strategy that cannot
beat a uniform random draw over the same universe, at the same instants, with the same
cost model, has not demonstrated a selection ability. It has demonstrated that the
universe went up.

Two properties are load-bearing.

**It selects, it does not predict.** The control emits the same *shape* of output as a
real strategy — same universe, same cap, same ranks, same side — so the two can be run
through one pipeline and compared without any special-casing. Any difference in the
results is therefore attributable to the selection and not to the plumbing.

**It is reproducible.** A control whose result moves between runs cannot settle an
argument, because a disappointing comparison can always be re-drawn. The seed is
explicit and the draw is derived from it deterministically, so the number a reviewer
sees is the number the author saw.
"""

from __future__ import annotations

import hashlib
import random
from datetime import datetime

from src.features.market_data.domain.entities.bar import Bar
from src.features.strategy.domain.entities.signal import Side, Signal, StrategySpec
from src.features.strategy.domain.repositories.strategy import Strategy, StrategyError
from src.features.strategy.infrastructure.refuted_hour_rule import (
    assert_usable_cross_section,
)
from src.shared.domain.clock import MARKET_TZ

DEFAULT_SEED = 20260615

EVIDENCE = "METHODS.md"
"""The "random null" gate. The control is the executable half of that line."""


class RandomControl(Strategy):
    """Uniform random selection from the cross-section at a fixed seed."""

    def __init__(self, seed: int = DEFAULT_SEED, side: Side = Side.BUY) -> None:
        """
        :param seed: fixes the draw. Same seed and same inputs give the same signals,
            in the same order, in any process.
        :param side: the direction the control trades. Defaults to BUY because the
            strategy it is shipped alongside is long-only, and a control that trades
            the other side is not measuring selection, it is measuring drift.
        """
        self.seed = seed
        self.side = side

    @property
    def spec(self) -> StrategySpec:
        return StrategySpec(
            key="random_control",
            name=f"Seeded random control (seed={self.seed})",
            verdict="CONTROL",
            summary=(
                "uniform random selection over the same universe at a fixed seed; "
                "a strategy that does not beat this has demonstrated nothing"
            ),
            evidence=EVIDENCE,
        )

    def evaluate(
        self,
        as_of: datetime,
        bars_by_symbol: dict[str, list[Bar]],
        max_signals: int,
    ) -> list[Signal]:
        """Draw up to `max_signals` names at random, ranked in draw order.

        The control runs under the same point-in-time guard as the strategy it is
        compared against. That is not defensive habit: if the control were permitted a
        malformed cross-section that the strategy refuses, the two would be evaluated
        over different samples and the comparison would silently stop being one.
        """
        assert_usable_cross_section(as_of, bars_by_symbol)
        if max_signals < 0:
            raise StrategyError(f"max_signals must not be negative, got {max_signals}")

        # A name with no bars is not tradable, and including it would let the control
        # "select" instruments the strategy could never have seen.
        candidates = sorted(symbol for symbol, bars in bars_by_symbol.items() if bars)
        if not candidates or max_signals == 0:
            return []

        rng = self._rng_for(as_of)
        drawn = rng.sample(candidates, k=min(max_signals, len(candidates)))

        return [
            Signal(
                symbol=symbol,
                side=self.side,
                timestamp=as_of,
                rank=position,
                reason=(
                    f"random control, seed={self.seed} — no view on {symbol}; this "
                    f"signal exists to give a candidate strategy something to beat"
                ),
            )
            for position, symbol in enumerate(drawn, start=1)
        ]

    def _rng_for(self, as_of: datetime) -> random.Random:
        """A generator fixed by (seed, instant).

        Per-instant rather than one long-lived generator so that a single evaluation is
        reproducible on its own: a reviewer re-running one day of a backtest gets the
        draw that day actually received, instead of a stream position that depends on
        how many days were replayed first.

        The instant is normalised to `MARKET_TZ` before it is hashed, so the same
        moment expressed in UTC and in exchange-local terms draws the same names — a
        control whose output depends on how the caller spelled a timezone would be a
        very quiet source of disagreement between a backtest and a live run.

        SHA-256 rather than `hash()`: string hashing is salted per process by
        `PYTHONHASHSEED`, which would make "seeded" a claim this class does not keep.
        """
        stamp = as_of.astimezone(MARKET_TZ).isoformat()
        digest = hashlib.sha256(f"{self.seed}|{stamp}".encode()).digest()
        return random.Random(int.from_bytes(digest[:8], "big"))
