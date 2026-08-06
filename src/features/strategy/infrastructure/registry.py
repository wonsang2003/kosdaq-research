"""Every strategy implementation this repository ships, and what each one is worth.

**There is no CONFIRMED strategy registered here, and there will not be one.** The
rules this project confirmed are running on the author's own capital. Publishing the
executable form of a live edge would tell a prospective employer that their edge would
not be safe either, so the disclosure policy in `docs/DISCLOSURE.md` withholds the
confirmatory material — thresholds, entry and exit timing, feature names — while
publishing the method and every failure in full.

That promise is made in prose elsewhere. Here it is made checkable. This module is the
complete list of what can be run, it is short enough to read in one screen, and
`tests/test_strategies.py::test_no_registered_strategy_claims_to_work` fails the build
if anything with a `CONFIRMED` verdict is ever added to it. A reader does not have to
take the claim on trust; they have to read one dict.

What is registered:

* `refuted_hour` — a rule selected on a training fold and then refuted out of sample,
  published in full because a refutation gives nothing away.
* `random_control` — the seeded random null the refuted rule is measured against.

Both declare `is_expected_to_lose`, and the runtime banner says so before it runs.
"""

from __future__ import annotations

from collections.abc import Callable

from src.features.strategy.domain.repositories.strategy import Strategy
from src.features.strategy.infrastructure.random_control import RandomControl
from src.features.strategy.infrastructure.refuted_hour_rule import RefutedHourRule
from src.shared.domain.errors import DomainError


class UnknownStrategy(DomainError):
    """A strategy key that is not registered was requested.

    Deliberately **not** a `StrategyError`. That one means "a strategy ran and could
    not produce a result", and a caller wrapping its evaluation loop in
    `except StrategyError` would swallow a typo'd configuration key as a bad trading
    day. A misconfigured run and a quiet market must not arrive at the same handler.
    """


STRATEGIES: dict[str, Callable[[], Strategy]] = {
    "refuted_hour": RefutedHourRule,
    "random_control": RandomControl,
}
"""Key -> zero-argument factory.

Factories rather than instances so that each run gets its own object. A module-level
singleton is fine today, because neither implementation holds mutable state, and would
stop being fine the first time one did — at which point a backtest and the live loop
would be sharing it, which is the class of drift this repository's composition root
exists to prevent.
"""

DEFAULT_STRATEGY_KEY = "refuted_hour"
"""What runs when nothing is configured.

The default is a documented failure on purpose: a reviewer who runs this repository
with no arguments should get the demonstration, complete with its banner, rather than
something that looks like an offer.
"""


def get_strategy(key: str) -> Strategy:
    """Build the registered strategy for `key`.

    :raise UnknownStrategy: naming the available keys. The message carries the list
        because the alternative — a bare `KeyError` from a config lookup — sends the
        reader to the wrong file, and because the list *is* the disclosure statement:
        seeing it is how one learns there are only two.
    """
    factory = STRATEGIES.get(key)
    if factory is None:
        raise UnknownStrategy(
            f"unknown strategy {key!r}; available keys: {', '.join(available_keys())}. "
            "No confirmed strategy is published in this repository — see docs/DISCLOSURE.md"
        )
    return factory()


def available_keys() -> tuple[str, ...]:
    """Registered keys, sorted so the banner and the error message never disagree."""
    return tuple(sorted(STRATEGIES))
