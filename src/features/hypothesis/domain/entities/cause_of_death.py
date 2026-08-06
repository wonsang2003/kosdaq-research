"""Cause-of-death taxonomy — why strategies died here.

This is a *prior*, not a label set. Its working use is the reverse of documentation:
when a new idea arrives, it is matched against these classes first, and a match is
grounds to not run the backtest at all. The original seven were frozen in
`research/STRATEGY_DISCOVERY_LOG.md` section 4 after roughly forty kills; the three
marked as extensions were added later, each because a strategy died in a way none
of the seven described.

Ordering below is by frequency observed across ~98 tested strategies in four
markets (KRX equities, Korean crypto arbitrage, Korea-stock perpetuals, Polymarket).
"""

from __future__ import annotations

from enum import Enum


class CauseOfDeath(str, Enum):
    """The reason a hypothesis was retired. One per verdict, naming the decisive test."""

    FILL_FANTASY = "fill_fantasy"
    """The backtest transacted at a price that cannot be obtained. The most common
    and most expensive class: predictive power tends to live exactly where the
    queue does not clear. Canonical case: a limit-up-lock strategy with
    Newey-West t = 48.0 over 13 consecutive profitable years that filled 0 of 20
    live orders and took 0% allocation across 32 closing auctions."""

    COST_FLOOR = "cost_floor"
    """The gross edge is real and statistically solid, but smaller than the
    friction it must cross. In Korea the binding term is usually the sell-side
    securities transaction tax (0.20% from 2026), which makes the round trip
    ~0.38%. An 11.5-year overnight excess of +0.094%/day with t = 10.2 is real
    alpha and still structurally unreachable by a taxed participant."""

    SURVIVORSHIP_MANUFACTURE = "survivorship_manufacture"
    """The edge was produced by the absence of delisted names rather than by the
    signal. Recomputing a small-cap bounce on a 185-name delisted panel moved
    day-clustered t from +13..48 to -5.7. The check is not optional in Korean
    small caps: it cut one gap-down edge by roughly 60% while leaving a
    corporate-event edge within 5% of itself, and that asymmetry is itself
    information."""

    MULTIPLICITY = "multiplicity"
    """*(extension)* The reported figure is the maximum of a search, not an
    estimate. Diagnostic: compare the winning t against the noise ceiling
    sqrt(2 ln k) for the number of cells actually tested. An argmax over 1,021
    filter combinations produced Sidak p = 1.0000; a 72-cell gap matrix landed at
    the 46th-94th percentile of its own best-of-72 null, i.e. worse than a random
    winner."""

    REGIME_CONCENTRATION = "regime_concentration"
    """Performance is confined to one market state and reads as a general edge only
    because the sample is dominated by it. Detected by leave-one-block-out and by
    checking what fraction of profit sits in the top few days. An overnight ETF
    edge that is positive every year from 2020 is negative in 2016-2019: it was
    born in a regime, and it will die with one."""

    UNOBSERVABLE_AT_DECISION = "unobservable_at_decision"
    """*(extension)* The conditioning variable is not knowable when the order must
    be placed. Distinct from ordinary look-ahead in that no code is wrong — the
    information simply does not exist yet. The opening gap strategy conditioned on
    the *realised* 09:00 gap; regressing it on the 08:57 indicative gap available at
    order time gives R-squared = 0.001 over n = 126, and 14 of 14 live orders went
    unfilled as a direct consequence."""

    INSTRUMENT_ABSENT = "instrument_absent"
    """The edge is real and reachable in principle, but no tradable instrument
    exposes it to this participant. The KOSDAQ composite short has t = 3.25 and no
    future written on it; ~76% of that alpha sits in names outside KOSDAQ 150,
    which by construction can never be included. Retail stock lending returned 46
    borrowable names, all KOSPI large caps, overlapping the edge names 0 of 30."""

    BETA_DISGUISE = "beta_disguise"
    """A market or style exposure reported as alpha. A defensive low-volatility
    basket showing '+12.9%' is holding beta, not skill. The separation is
    mechanical — subtract beta times the contemporaneous market return — and it
    cuts both ways: the same adjustment that deflates a fake winner rescued a real
    strategy whose small negative raw forward return sat inside a market that
    had fallen several times as far."""

    ADVERSE_SELECTION = "adverse_selection"
    """*(extension)* Fills are obtainable, but the subset that fills is the subset
    that loses. Distinguished from FILL_FANTASY by the fill rate being non-zero and
    the *conditional* outcome being the problem. Measured directly: the 87 orders
    that could not be filled had an ideal net of +2.23% against +0.70% for the 220
    that did fill — the trades you cannot get are three times better than the ones
    you get."""

    NOTHING_THERE = "nothing_there"
    """No effect survived first contact. Kept as an explicit class so that null
    results are recorded rather than forgotten and silently re-tested. Members
    include per-stock elasticity constants, flow persistence, PEAD, day-of-week,
    tail ratios, and most sector, pair, and lead-lag work."""

    @property
    def is_execution_class(self) -> bool:
        """True when the signal was real and execution killed it.

        This partition matters more than it looks. An execution-class death means
        the research was correct and the instrument was wrong, so the finding may
        transfer to a different venue, size, or account type. The other classes
        mean the finding itself was never there.
        """
        return self in (
            CauseOfDeath.FILL_FANTASY,
            CauseOfDeath.COST_FLOOR,
            CauseOfDeath.INSTRUMENT_ABSENT,
            CauseOfDeath.ADVERSE_SELECTION,
        )

    @property
    def is_extension(self) -> bool:
        """True for classes added after the original seven were frozen."""
        return self in (
            CauseOfDeath.MULTIPLICITY,
            CauseOfDeath.UNOBSERVABLE_AT_DECISION,
            CauseOfDeath.ADVERSE_SELECTION,
        )
