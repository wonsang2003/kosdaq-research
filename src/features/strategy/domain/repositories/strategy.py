"""The strategy port — the boundary the alpha does not cross.

This is the single most important interface in the repository, and its value is
mostly in what is *not* on the other side of it.

The strategies that this project confirmed are running on the author's own capital.
They are not in this repository and will not be. Stating that in prose is a claim a
reader has to take on faith. Stating it as an interface makes it checkable: the
pipeline codes against `Strategy`, the composition root binds whichever
implementation is configured, and the shipped implementations are a **refuted** rule
and a **random control**. A reviewer can verify by reading `infrastructure/` that
there is nothing else there.

That is also why this is an interface rather than a configuration flag. The system
being imitated had no seam here at all — swapping the strategy meant editing the
scoring module, which is why its "live" path and its "backtest" path had quietly
drifted into computing different things.

### The contract that matters

`evaluate` is given bars **already truncated to the decision instant** and must not
reach for anything else. Point-in-time discipline is enforced upstream, at the one
place data is sliced, rather than trusted to every implementation — because a
strategy that peeks produces a beautiful result and no error.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from src.features.market_data.domain.entities.bar import Bar
from src.features.strategy.domain.entities.signal import Signal, StrategySpec
from src.shared.domain.errors import DomainError


class StrategyError(DomainError):
    """A strategy could not evaluate.

    Raised rather than returning no signals. "No opportunities today" and "the
    strategy crashed" must not be the same observation — the first is a result and
    the second is an incident, and a system that conflates them reports a quiet week
    while it is broken.
    """


class Strategy(ABC):
    """A rule that turns a point-in-time market view into trade intents."""

    @property
    @abstractmethod
    def spec(self) -> StrategySpec:
        """Identity and disclosure status. The runtime prints this before it runs, so
        a reviewer is told what they are about to execute."""
        raise NotImplementedError

    @abstractmethod
    def evaluate(
        self,
        as_of: datetime,
        bars_by_symbol: dict[str, list[Bar]],
        max_signals: int,
    ) -> list[Signal]:
        """Emit trade intents for one instant.

        :param as_of: the decision instant. Every bar supplied is already at or before
            this — the caller has truncated. An implementation must not fetch, look
            up, or otherwise obtain data outside `bars_by_symbol`; doing so
            reintroduces look-ahead at the one layer that cannot check for it.
        :param bars_by_symbol: the cross-section. Whole-universe rather than
            per-symbol on purpose: ranking is relative, and a strategy that can only
            see one name cannot rank.
        :param max_signals: hard cap on returned signals.
        :return: at most `max_signals` signals, ranked 1..n with no gaps, all stamped
            `as_of`. An **empty list is a valid and expected result** meaning no
            opportunity — distinct from an exception.
        :raise StrategyError: if evaluation fails. Never return `[]` to indicate a
            failure.
        """
        raise NotImplementedError

    def describe(self) -> str:
        """One-line banner for the runtime.

        Concrete rather than abstract because every implementation would write the
        same thing, and because the warning for an expected-to-lose strategy must be
        impossible to omit by forgetting to override.
        """
        spec = self.spec
        banner = f"[{spec.verdict}] {spec.name} — {spec.summary}"
        if spec.is_expected_to_lose:
            banner += "\n  NOTE: this strategy is documented as not working. Losses below are the expected result."
        return banner
