"""Refusal tests — the judgment calls, encoded so they cannot be quietly reversed.

This repository's thesis is disposal, so the disposal is what gets tested. Every
assertion here is that some operation **raises** rather than returning a plausible
value, and each one corresponds to a real incident where a plausible value was
returned and a wrong conclusion followed.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.features.execution_realism.domain.services.fill_feasibility import (
    FillObstruction,
    Unfillable,
    check_buy,
    participation_rate,
)
from src.features.preregistration.domain.entities.frozen_rule import (
    FrozenRule,
    FrozenRuleViolation,
    JudgmentCriteria,
)

FROZEN_AT = datetime(2020, 1, 15, 18, 30)


def _rule() -> FrozenRule:
    return FrozenRule(
        label="R1",
        filters=("a", "b", "c"),
        frozen_at=FROZEN_AT,
        search_width=1021,
        criteria=JudgmentCriteria(horizon_sessions=15),
    )


# ── pre-registration ─────────────────────────────────────────────────────────
def test_a_frozen_rule_cannot_be_mutated():
    """Adding a filter after the freeze re-opens the search and invalidates the bar.

    The recorded search width stays stale while the effective one grows, so the
    significance bar the rule is judged against is one it has already cleared.
    """
    with pytest.raises(FrozenRuleViolation, match="re-opens a search"):
        _rule().with_filter("d")


def test_frozen_rule_fields_are_immutable():
    rule = _rule()
    with pytest.raises(Exception):
        rule.search_width = 5


def test_there_is_no_confirm_line():
    """The absence is structural, not an oversight.

    Forward testing at realistic event rates cannot reach significance inside a usable
    horizon, so a threshold that would 'confirm' a rule cannot honestly be set. A field
    that could hold one would eventually hold one.
    """
    criteria = JudgmentCriteria(horizon_sessions=15, kill_thresholds={20: -1.5})
    assert criteria.confirm_threshold is None
    assert not hasattr(criteria, "confirm_thresholds")


def test_a_rule_may_not_be_judged_on_the_data_that_produced_it():
    rule = _rule()
    rule.assert_judgeable_on(FROZEN_AT + timedelta(days=1))
    with pytest.raises(FrozenRuleViolation, match="predates the freeze"):
        rule.assert_judgeable_on(FROZEN_AT - timedelta(seconds=1))


def test_search_width_is_recorded_at_freeze_time():
    """k must include the cells abandoned early, or the bar is understated."""
    assert _rule().search_width == 1021


# ── fill feasibility ─────────────────────────────────────────────────────────
def test_a_limit_up_locked_close_is_not_a_buyable_price():
    """The kill that produced this module.

    A strategy with a Newey-West t of 48.0 across thirteen consecutive profitable years
    filled zero of twenty live orders, because at the limit price the buy queue is
    enormous by construction and pro-rata allocation to a small order rounds to nothing.
    """
    check = check_buy(price=13_000, prev_close=10_000, opposing_size=999_999)
    assert not check.feasible
    assert check.obstruction is FillObstruction.LIMIT_LOCKED
    with pytest.raises(Unfillable, match="limit_locked"):
        check.raise_if_infeasible()


def test_an_unknown_book_counts_against_feasibility():
    """`None` is not an absent obstruction. An unevaluable book is not a clear one."""
    assert not check_buy(price=11_000, prev_close=10_000, opposing_size=None).feasible


def test_zero_ask_size_is_locked_not_merely_thin():
    """A locked book reports an ask of zero rather than an absent ask.

    A naive `ask > 0` filter therefore discards the entire event instead of flagging it
    — that exact filter once cut a sample from 69 events to 3 and produced a fabricated
    negative result that no local unit test could have caught.
    """
    check = check_buy(price=11_000, prev_close=10_000, opposing_size=0)
    assert not check.feasible
    assert check.obstruction is FillObstruction.NO_OPPOSING_SIZE


def test_a_signal_confirmed_at_the_price_it_transacts_is_a_ghost_leg():
    """A futures fade at t = 3.71 entered at a print its own signal needed to exist.

    Re-run on the first executable leg it fell to t = 1.34.
    """
    check = check_buy(
        price=11_000, prev_close=10_000, opposing_size=1_000,
        signal_time=1000.0, price_time=1000.0,
    )
    assert not check.feasible
    assert check.obstruction is FillObstruction.SIGNAL_AFTER_PRICE


def test_an_ordinary_buy_below_the_limit_is_feasible():
    assert check_buy(price=11_000, prev_close=10_000, opposing_size=5_000,
                     signal_time=999.0, price_time=1000.0).feasible


def test_participation_rate_raises_on_an_empty_window():
    """A zero denominator means there was no volume to participate in.

    That is a fill problem, not a division problem, and returning infinity or zero would
    let it pass as a number.
    """
    with pytest.raises(Unfillable, match="no traded value"):
        participation_rate(order_value=1_000_000, traded_value=0)


def test_participation_rate_against_the_right_denominator():
    """The window the order competes in, not the daily figure.

    Using a daily denominator against an auction entry overstated capacity by roughly
    sixty-nine times in one measured case; enforcing a 10% cap afterwards cut realised
    value per trade by 39%.
    """
    daily = participation_rate(1_000_000, 1_000_000_000)
    auction = participation_rate(1_000_000, 14_000_000)
    assert daily < 0.002
    assert auction > 0.05, "the same order is a large fraction of the auction it enters"
