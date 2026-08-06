"""Execution: the simulated broker and the journal a restart reads.

Every assertion here names the failure it guards. The headline one is
`test_a_kill_and_restart_restores_working_orders_and_positions_identically`: the
imitated system journalled every order and every fill and never read either back, so
a restart came up flat with a complete account of its own positions sitting unread on
disk. That is not a subtle bug — it is a write-only journal, and the only way to keep
it from recurring is a test that kills the process and demands the state back.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

import pytest

from src.features.execution.domain.entities.order import Fill, Order, OrderState, Position
from src.features.execution.domain.repositories.broker import (
    BrokerUnavailable,
    OrderRejected,
)
from src.features.execution.infrastructure.jsonl_journal import JsonlOrderJournal
from src.features.execution.infrastructure.simulated_broker import SimulatedBroker
from src.features.market_data.domain.entities.bar import Quote
from src.features.operations.infrastructure.atomic_store import StateUnavailable
from src.features.operations.infrastructure.controls import (
    FileKillSwitch,
    KillSwitchEngaged,
)
from src.features.strategy.domain.entities.signal import Side
from src.shared.domain import clock

T0 = datetime(2026, 6, 15, 9, 5, tzinfo=clock.MARKET_TZ)
"""A Monday, inside the session. Stated in exchange terms so the suite passes under
TZ=UTC, which is what the container actually runs as."""


def _order(
    order_id: str,
    *,
    symbol: str = "000660",
    side: Side = Side.BUY,
    quantity: float = 100,
    limit_price: float | None = 1000.0,
    key: str = "",
    at: datetime = T0,
) -> Order:
    return Order(
        order_id=order_id,
        symbol=symbol,
        side=side,
        quantity=quantity,
        limit_price=limit_price,
        created_at=at,
        idempotency_key=key,
    )


def _quote(
    symbol: str = "000660",
    *,
    bid: float = 999.0,
    ask: float = 1000.0,
    bid_size: float = 0.0,
    ask_size: float = 0.0,
    at: datetime = T0,
) -> Quote:
    return Quote(
        symbol=symbol,
        timestamp=at,
        bid=bid,
        ask=ask,
        bid_size=bid_size,
        ask_size=ask_size,
    )


# ── identity ─────────────────────────────────────────────────────────────────
def test_the_simulator_admits_it_places_no_real_orders():
    """The runtime banner is derived from this. A simulator answering True, or a live
    adapter answering False, makes the banner a lie — so it is asserted, not trusted."""
    broker = SimulatedBroker()
    assert broker.name == "simulated"
    assert broker.places_real_orders is False


# ── fills ────────────────────────────────────────────────────────────────────
def test_a_crossing_quote_fills_on_submit():
    broker = SimulatedBroker(initial_cash=1_000_000)
    placed = broker.submit(_order("o1"), _quote(ask=1000.0, ask_size=100))

    assert placed.state is OrderState.FILLED
    assert placed.filled_quantity == 100
    assert placed.average_fill_price == 1000.0


def test_an_order_submitted_without_a_quote_rests_until_one_arrives():
    """Submission and execution are separate events. Collapsing them is how a
    backtest acquires fills at prices that were never offered."""
    broker = SimulatedBroker(initial_cash=1_000_000)
    resting = broker.submit(_order("o1"))
    assert resting.state is OrderState.OPEN
    assert broker.working_orders() == [resting]

    fills = broker.on_quote(_quote(ask=1000.0, ask_size=100))

    assert [f.quantity for f in fills] == [100]
    assert broker.get_order("o1").state is OrderState.FILLED
    assert broker.working_orders() == []


def test_a_partial_fill_accumulates_the_correct_vwap():
    """Two executions at different prices must produce the size-weighted average, not
    the last price and not the arithmetic mean. This number is the cost basis every
    downstream profit figure is measured against."""
    broker = SimulatedBroker(initial_cash=1_000_000)
    broker.submit(_order("o1", quantity=300, limit_price=1010.0))

    broker.on_quote(_quote(ask=1000.0, ask_size=100, at=T0))
    partial = broker.get_order("o1")
    assert partial.state is OrderState.PARTIALLY_FILLED
    assert partial.filled_quantity == 100
    assert partial.remaining_quantity == 200
    assert partial.average_fill_price == 1000.0

    broker.on_quote(_quote(ask=1004.0, ask_size=200, at=T0 + timedelta(minutes=1)))
    complete = broker.get_order("o1")

    assert complete.state is OrderState.FILLED
    assert complete.filled_quantity == 300
    assert complete.average_fill_price == pytest.approx((100 * 1000 + 200 * 1004) / 300)
    assert complete.average_fill_price != pytest.approx(1002.0), "arithmetic mean, not VWAP"


def test_a_terminal_order_refuses_further_fills():
    """A double-fill must be a raised error rather than a silently doubled position:
    the position is wrong either way, but only one of the two is discoverable."""
    broker = SimulatedBroker(initial_cash=1_000_000)
    broker.submit(_order("o1"), _quote(ask=1000.0, ask_size=100))
    assert broker.get_order("o1").state is OrderState.FILLED

    with pytest.raises(OrderRejected, match="cannot take further fills"):
        broker.apply_fill("o1", 100, 1000.0, T0)

    assert broker.on_quote(_quote(ask=1000.0, ask_size=100)) == []
    assert broker.positions()["000660"].quantity == 100


def test_a_cancelled_order_receives_no_further_fills():
    broker = SimulatedBroker(initial_cash=1_000_000)
    broker.submit(_order("o1"))
    broker.cancel("o1")

    assert broker.on_quote(_quote(ask=1000.0, ask_size=100)) == []
    assert broker.get_order("o1").state is OrderState.CANCELLED
    assert broker.positions() == {}


def test_an_overfill_is_refused_rather_than_clamped():
    """Clamping produces a book that looks correct and hides whatever generated the
    extra shares. The extra shares are the signal."""
    broker = SimulatedBroker(initial_cash=1_000_000)
    broker.submit(_order("o1", quantity=100))

    with pytest.raises(OrderRejected, match="overfill"):
        broker.apply_fill("o1", 150, 1000.0, T0)

    assert broker.get_order("o1").filled_quantity == 0


def test_a_book_reporting_no_size_fills_nothing():
    """The original read `ask_size == 0` as 'size unknown' and filled the whole order.
    Zero offered means zero shares — `Quote.is_locked` says so, and a simulator that
    fills against liquidity nobody offered is the fill fantasy this repo documents."""
    broker = SimulatedBroker(initial_cash=1_000_000)
    resting = broker.submit(_order("o1"), _quote(ask=1000.0, ask_size=0))

    assert resting.state is OrderState.OPEN
    assert broker.on_quote(_quote(ask=1000.0, ask_size=0)) == []
    assert broker.positions() == {}


def test_a_locked_book_with_no_offer_fills_nothing():
    """An ask of zero is a limit-locked instrument with no seller, not a free share."""
    broker = SimulatedBroker(initial_cash=1_000_000)
    broker.submit(_order("o1"))
    assert broker.on_quote(_quote(ask=0.0, ask_size=500)) == []
    assert broker.get_order("o1").state is OrderState.OPEN


def test_a_non_marketable_limit_does_not_fill():
    """Both sides. A buy limit below the ask and a sell limit above the bid are
    resting orders, not executions — the venue would not cross either."""
    broker = SimulatedBroker(initial_cash=1_000_000)
    broker.submit(_order("b1", quantity=100), _quote(ask=1000.0, ask_size=100))

    broker.submit(_order("b2", limit_price=990.0))
    broker.submit(_order("s1", side=Side.SELL, quantity=100, limit_price=1010.0))

    assert broker.on_quote(_quote(bid=999.0, ask=1000.0, bid_size=500, ask_size=500)) == []
    assert broker.get_order("b2").state is OrderState.OPEN
    assert broker.get_order("s1").state is OrderState.OPEN


def test_one_quotes_size_is_not_handed_to_two_orders():
    """120 offered shares cannot fill two orders of 100. The original gave each
    resting order `min(remaining, ask_size)` from the same quote."""
    broker = SimulatedBroker(initial_cash=1_000_000)
    broker.submit(_order("first", quantity=100))
    broker.submit(_order("second", quantity=100))

    fills = broker.on_quote(_quote(ask=1000.0, ask_size=120))

    assert [f.quantity for f in fills] == [100, 20]
    assert broker.get_order("first").state is OrderState.FILLED
    assert broker.get_order("second").state is OrderState.PARTIALLY_FILLED
    assert broker.positions()["000660"].quantity == 120


def test_a_quote_for_a_different_instrument_is_refused():
    """Passing the wrong book is how a fill at another instrument's price gets in."""
    broker = SimulatedBroker(initial_cash=1_000_000)
    with pytest.raises(OrderRejected, match="quote is for"):
        broker.submit(_order("o1", symbol="000660"), _quote("035720", ask_size=100))


def test_a_fill_timestamp_comes_from_the_quote_not_the_wall_clock(monkeypatch):
    """The fill happened when the book showed it, not when the simulator ran."""
    monkeypatch.setattr(clock, "now", lambda: datetime(2030, 1, 1, tzinfo=clock.MARKET_TZ))
    broker = SimulatedBroker(initial_cash=1_000_000)
    broker.submit(_order("o1"), _quote(ask=1000.0, ask_size=100, at=T0))
    assert broker.fills()[0].timestamp == T0


# ── pre-trade validation ─────────────────────────────────────────────────────
def test_a_naked_short_is_refused():
    """Selling what is not held. The refusal comes back as a REJECTED order rather
    than an exception, because a rejection is a normal outcome that must be journalled
    like any other."""
    broker = SimulatedBroker(initial_cash=1_000_000)
    refused = broker.submit(_order("s1", side=Side.SELL, quantity=100))

    assert refused.state is OrderState.REJECTED
    assert "naked short" in refused.reason
    assert broker.working_orders() == []


def test_selling_what_a_working_order_already_promised_is_refused():
    """Two sells of 60 against one holding of 100 each pass an unencumbered check and
    together go short. Working orders reserve the shares they will consume."""
    broker = SimulatedBroker(initial_cash=1_000_000)
    broker.submit(_order("b1", quantity=100), _quote(ask=1000.0, ask_size=100))

    first = broker.submit(_order("s1", side=Side.SELL, quantity=60, limit_price=1010.0))
    second = broker.submit(_order("s2", side=Side.SELL, quantity=60, limit_price=1010.0))

    assert first.state is OrderState.OPEN
    assert second.state is OrderState.REJECTED
    assert "40" in second.reason


def test_insufficient_cash_is_refused():
    broker = SimulatedBroker(initial_cash=50_000)
    refused = broker.submit(_order("o1", quantity=100, limit_price=1000.0))

    assert refused.state is OrderState.REJECTED
    assert "insufficient cash" in refused.reason


def test_cash_promised_to_a_working_order_cannot_be_spent_twice():
    broker = SimulatedBroker(initial_cash=150_000)
    first = broker.submit(_order("o1", quantity=100, limit_price=1000.0))
    second = broker.submit(_order("o2", quantity=100, limit_price=1000.0))

    assert first.state is OrderState.OPEN
    assert second.state is OrderState.REJECTED
    assert "insufficient cash" in second.reason


def test_a_fractional_quantity_is_refused():
    """KRX has no fractional shares. A quantity the simulator accepts and the venue
    truncates makes every fill-rate number computed against it quietly wrong."""
    broker = SimulatedBroker(initial_cash=1_000_000)
    refused = broker.submit(_order("o1", quantity=1.5))

    assert refused.state is OrderState.REJECTED
    assert "fractional" in refused.reason


def test_a_non_positive_limit_price_is_refused():
    """`Order.limit_price` carries no pydantic constraint because None means market
    order, so a zero has to be caught here or it becomes a free share."""
    broker = SimulatedBroker(initial_cash=1_000_000)
    refused = broker.submit(_order("o1", limit_price=0.0))

    assert refused.state is OrderState.REJECTED
    assert "limit_price" in refused.reason


def test_an_unpriced_buy_is_refused_when_cash_is_enforced():
    """Affordability cannot be checked on an unpriced order, and checking it after the
    fill means checking it after the money is gone."""
    strict = SimulatedBroker(initial_cash=1_000_000, enforce_cash=True)
    refused = strict.submit(_order("o1", limit_price=None))
    assert refused.state is OrderState.REJECTED
    assert "market buy" in refused.reason

    relaxed = SimulatedBroker(initial_cash=0.0, enforce_cash=False)
    filled = relaxed.submit(_order("o2", limit_price=None), _quote(ask=1000.0, ask_size=100))
    assert filled.state is OrderState.FILLED
    assert filled.average_fill_price == 1000.0


def test_a_rejection_is_journalled_like_any_other_transition(tmp_path):
    """An unjournalled rejection is invisible to whoever reads the file after a bad
    day, and 'the order is simply absent' is the least debuggable state there is."""
    journal = JsonlOrderJournal(tmp_path / "orders.jsonl", broker_name="simulated")
    broker = SimulatedBroker(initial_cash=0.0, journal=journal)
    broker.submit(_order("o1"))

    orders, fills = journal.replay()
    assert fills == []
    assert [o.state for o in orders] == [OrderState.REJECTED]
    assert orders[0].reason


# ── idempotency ──────────────────────────────────────────────────────────────
def test_a_duplicate_idempotency_key_is_refused():
    """The composite key is what makes the scheduler safe to re-fire, which is what
    makes a restart safe. A second submission under the same key raises rather than
    returning a REJECTED order, because it is a caller mistake, not a market outcome."""
    broker = SimulatedBroker(initial_cash=1_000_000)
    broker.submit(_order("o1", key="2010-06-14|SYN001|entry"))

    with pytest.raises(OrderRejected, match="duplicate idempotency_key"):
        broker.submit(_order("o2", key="2010-06-14|SYN001|entry"))


def test_a_rejected_order_does_not_burn_its_idempotency_key():
    """A rejection placed no risk. Consuming the key there turns a momentary refusal
    into a permanent one for that intent for the rest of the day."""
    broker = SimulatedBroker(initial_cash=1_000_000)
    refused = broker.submit(_order("s1", side=Side.SELL, quantity=100, key="k"))
    assert refused.state is OrderState.REJECTED

    broker.submit(_order("b1", quantity=100), _quote(ask=1000.0, ask_size=100))
    retried = broker.submit(_order("s2", side=Side.SELL, quantity=100, limit_price=1010.0, key="k"))
    assert retried.state is OrderState.OPEN


def test_a_duplicate_order_id_is_refused():
    broker = SimulatedBroker(initial_cash=1_000_000)
    broker.submit(_order("o1"))
    with pytest.raises(OrderRejected, match="duplicate order_id"):
        broker.submit(_order("o1"))


def test_resubmitting_an_already_submitted_order_is_refused():
    broker = SimulatedBroker(initial_cash=1_000_000)
    placed = broker.submit(_order("o1"))
    with pytest.raises(OrderRejected, match="already submitted"):
        broker.submit(placed.model_copy(update={"order_id": "o2"}))


# ── the kill switch ──────────────────────────────────────────────────────────
def test_the_kill_switch_blocks_submission_and_the_exception_escapes(tmp_path):
    """Not caught and not converted into a REJECTED order: submission is the one path
    the switch exists to stop, and a caught halt is a log line."""
    switch = FileKillSwitch(tmp_path / "kill.flag")
    broker = SimulatedBroker(initial_cash=1_000_000, kill_switch=switch)
    switch.engage("manual halt")

    with pytest.raises(KillSwitchEngaged, match="manual halt"):
        broker.submit(_order("o1"))

    assert broker.all_orders() == []


def test_the_kill_switch_does_not_block_cancellation(tmp_path):
    """The most important asymmetry in the system. A switch that froze the exit path
    would strand open positions, turning 'stop trading' into an unhedged hold."""
    switch = FileKillSwitch(tmp_path / "kill.flag")
    broker = SimulatedBroker(initial_cash=1_000_000, kill_switch=switch)
    broker.submit(_order("o1"))
    switch.engage("halt everything")

    cancelled = broker.cancel("o1")

    assert cancelled.state is OrderState.CANCELLED
    assert broker.working_orders() == []


# ── cancellation ─────────────────────────────────────────────────────────────
def test_cancelling_a_terminal_order_returns_it_unchanged():
    """Cancel is idempotent because the caller often cannot know whether a previous
    attempt landed."""
    broker = SimulatedBroker(initial_cash=1_000_000)
    broker.submit(_order("o1"), _quote(ask=1000.0, ask_size=100))

    assert broker.cancel("o1").state is OrderState.FILLED
    assert broker.cancel("o1").state is OrderState.FILLED


def test_cancelling_an_unknown_order_raises():
    """A synthetic CANCELLED order for an id the broker never saw would let a
    liquidation loop believe it closed something that is still open."""
    with pytest.raises(OrderRejected, match="unknown order_id"):
        SimulatedBroker().cancel("never-existed")


# ── the book ─────────────────────────────────────────────────────────────────
def test_a_symbol_that_has_gone_flat_is_not_a_holding():
    broker = SimulatedBroker(initial_cash=1_000_000)
    broker.submit(_order("b1", quantity=100), _quote(ask=1000.0, ask_size=100))
    broker.submit(
        _order("s1", side=Side.SELL, quantity=100, limit_price=1000.0),
        _quote(bid=1005.0, ask=1006.0, bid_size=100),
    )

    assert broker.positions() == {}
    assert broker.cash == pytest.approx(1_000_000 - 100 * 1000 + 100 * 1005)


def test_cash_and_position_move_together_on_every_fill():
    broker = SimulatedBroker(initial_cash=1_000_000)
    broker.submit(_order("b1", quantity=100, limit_price=1010.0), _quote(ask=1000.0, ask_size=60))

    assert broker.cash == pytest.approx(940_000)
    assert broker.positions() == {
        "000660": Position(symbol="000660", quantity=60, average_price=1000.0)
    }


def test_positions_raise_rather_than_reporting_a_false_flat():
    """`{}` reads as 'flat', and acting on a false flat means re-entering a position
    already held. Absence and failure are different answers."""
    broker = SimulatedBroker(initial_cash=1_000_000)
    broker.submit(_order("b1", quantity=100), _quote(ask=1000.0, ask_size=100))
    broker.mark_state_unknown("venue position report disagrees with the journal")

    with pytest.raises(BrokerUnavailable, match="not reconciled"):
        broker.positions()
    assert not broker.state_known


def test_an_unreconciled_book_refuses_new_risk_but_still_reports_exposure():
    """Placing new orders on top of a position you cannot state is how a bad restart
    compounds. Working orders are still listed, because that is what the operator
    needs to see first."""
    broker = SimulatedBroker(initial_cash=1_000_000)
    resting = broker.submit(_order("o1"))
    broker.mark_state_unknown("replay did not add up")

    with pytest.raises(BrokerUnavailable):
        broker.submit(_order("o2"))
    assert broker.working_orders() == [resting]


def test_a_replay_that_does_not_add_up_leaves_the_book_unreconciled():
    """Two independent accounts of the same history — each order's filled_quantity and
    the fills themselves — must agree. When they do not, the broker refuses instead of
    assembling a book from records that contradict each other."""
    orders = [_order("o1").model_copy(update={"state": OrderState.OPEN})]
    ghost = Fill(
        order_id="never-journalled",
        symbol="000660",
        side=Side.BUY,
        quantity=10,
        price=1000.0,
        timestamp=T0,
    )

    broker = SimulatedBroker.restore_from(orders, [ghost], initial_cash=1_000_000)

    assert not broker.state_known
    assert any("never-journalled" in problem for problem in broker.problems)
    with pytest.raises(BrokerUnavailable):
        broker.positions()


# ── the journal ──────────────────────────────────────────────────────────────
def test_a_missing_journal_is_an_ordinary_first_run(tmp_path):
    """Missing and unreadable are different. Only one of them is an incident."""
    assert JsonlOrderJournal(tmp_path / "absent.jsonl").replay() == ([], [])


def test_replay_returns_each_order_in_its_final_state(tmp_path):
    """The journal is a transition log, not a snapshot: one order appears OPEN, then
    PARTIALLY_FILLED, then FILLED."""
    journal = JsonlOrderJournal(tmp_path / "orders.jsonl", broker_name="simulated")
    broker = SimulatedBroker(initial_cash=1_000_000, journal=journal)
    broker.submit(_order("o1", quantity=300, limit_price=1010.0))
    broker.on_quote(_quote(ask=1000.0, ask_size=100))
    broker.on_quote(_quote(ask=1000.0, ask_size=200, at=T0 + timedelta(minutes=1)))

    orders, fills = journal.replay()

    assert len(orders) == 1, "four records, one order"
    assert orders[0].state is OrderState.FILLED
    assert orders[0].filled_quantity == 300
    assert [f.quantity for f in fills] == [100, 200]


def test_a_torn_final_line_still_replays_the_preceding_records(tmp_path):
    """A process killed mid-append leaves a partial last line. Losing one record is
    survivable; losing the file because of it is not."""
    path = tmp_path / "orders.jsonl"
    journal = JsonlOrderJournal(path, broker_name="simulated")
    journal.record_order(_order("o1").model_copy(update={"state": OrderState.OPEN}))
    journal.record_order(_order("o2").model_copy(update={"state": OrderState.OPEN}))
    with open(path, "a", encoding="utf-8") as handle:
        handle.write('{"kind": "order", "payload": {"order_i')

    orders, fills = journal.replay()

    assert [o.order_id for o in orders] == ["o1", "o2"]
    assert fills == []


def test_an_unreadable_journal_raises_instead_of_replaying_empty(tmp_path):
    """An empty result here claims 'no open orders', and a system that starts flat
    when it is not flat re-enters positions it already holds."""
    path = tmp_path / "orders.jsonl"
    journal = JsonlOrderJournal(path)
    journal.record_order(_order("o1").model_copy(update={"state": OrderState.OPEN}))
    os.chmod(path, 0o000)
    try:
        with pytest.raises(StateUnavailable):
            journal.replay()
    finally:
        os.chmod(path, 0o600)


def test_an_unrecognised_record_kind_raises_rather_than_being_skipped(tmp_path):
    """Skipping an unreadable record drops an order, and a dropped order is an
    untracked live position."""
    path = tmp_path / "orders.jsonl"
    path.write_text('{"kind": "something_else", "payload": {}}\n', encoding="utf-8")
    with pytest.raises(StateUnavailable, match="unknown kind"):
        JsonlOrderJournal(path).replay()


def test_a_payload_that_no_longer_validates_raises(tmp_path):
    path = tmp_path / "orders.jsonl"
    path.write_text('{"kind": "order", "payload": {"order_id": "o1"}}\n', encoding="utf-8")
    with pytest.raises(StateUnavailable, match="does not validate"):
        JsonlOrderJournal(path).replay()


def test_every_record_carries_the_broker_that_produced_it(tmp_path):
    """Simulated and live rows can legitimately share a file across a promotion, and a
    row that does not say which broker wrote it is unclassifiable forever after."""
    path = tmp_path / "orders.jsonl"
    journal = JsonlOrderJournal(path, broker_name="simulated")
    broker = SimulatedBroker(initial_cash=1_000_000, journal=journal)
    broker.submit(_order("o1"), _quote(ask=1000.0, ask_size=100))

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    assert records, "nothing was journalled"
    assert {r["broker"] for r in records} == {"simulated"}
    assert {r["kind"] for r in records} == {"order", "fill"}


def test_journalled_timestamps_survive_the_round_trip_with_their_zone(tmp_path):
    """A naive datetime coming back out of the journal would compare wrongly against
    every session gate — silently, since a gate that never opens logs nothing."""
    journal = JsonlOrderJournal(tmp_path / "orders.jsonl")
    journal.record_order(_order("o1").model_copy(update={"state": OrderState.OPEN}))
    orders, _ = journal.replay()

    assert orders[0].created_at.tzinfo is not None
    assert orders[0].created_at == T0


# ── restart ──────────────────────────────────────────────────────────────────
def test_a_kill_and_restart_restores_working_orders_and_positions_identically(tmp_path):
    """The failure this whole module exists for.

    The imitated system journalled orders and fills to CSV, kept positions in an
    in-memory dict, and never read either file back. A restart therefore came up flat
    and orderless with a complete account of both on disk one directory away. Here the
    process is killed mid-session with one filled order, one partially filled order,
    and one resting order, and the rebuilt broker must agree with the dead one exactly.
    """
    path = tmp_path / "orders.jsonl"
    journal = JsonlOrderJournal(path, broker_name="simulated")
    live = SimulatedBroker(initial_cash=10_000_000, journal=journal)

    live.submit(
        _order("a1", symbol="000111", quantity=100, limit_price=1000.0, key="d|000111|entry"),
        _quote("000111", ask=1000.0, ask_size=100),
    )
    live.submit(
        _order("b1", symbol="000222", quantity=200, limit_price=2000.0, key="d|000222|entry"),
        _quote("000222", ask=2000.0, ask_size=50),
    )
    live.submit(
        _order("c1", symbol="000333", quantity=10, limit_price=3000.0, key="d|000333|entry")
    )

    expected_positions = live.positions()
    expected_working = live.working_orders()
    expected_cash = live.cash
    assert len(expected_positions) == 2
    assert [o.order_id for o in expected_working] == ["b1", "c1"]

    # The process dies here. A restart that does not read the journal is the bug:
    forgetful = SimulatedBroker(initial_cash=10_000_000)
    assert forgetful.positions() == {} and forgetful.working_orders() == []

    orders, fills = JsonlOrderJournal(path).replay()
    restored = SimulatedBroker.restore_from(orders, fills, initial_cash=10_000_000)

    assert restored.state_known, restored.problems
    assert restored.positions() == expected_positions
    assert restored.working_orders() == expected_working
    assert restored.cash == pytest.approx(expected_cash)


def test_a_restored_broker_still_refuses_a_job_that_re_fires_after_the_restart(tmp_path):
    """The restart is exactly when a scheduler re-runs the job it was killed during.
    If the keys did not survive the replay, the recovery itself would double the
    position it just recovered."""
    path = tmp_path / "orders.jsonl"
    journal = JsonlOrderJournal(path, broker_name="simulated")
    live = SimulatedBroker(initial_cash=10_000_000, journal=journal)
    live.submit(_order("a1", key="2010-06-14|SYN001|entry"), _quote(ask=1000.0, ask_size=100))

    orders, fills = JsonlOrderJournal(path).replay()
    restored = SimulatedBroker.restore_from(orders, fills, initial_cash=10_000_000)

    with pytest.raises(OrderRejected, match="duplicate idempotency_key"):
        restored.submit(_order("a2", key="2010-06-14|SYN001|entry"))


def test_a_restored_broker_keeps_selling_from_the_recovered_position(tmp_path):
    """The recovered position must be real enough to sell out of. If it were not, the
    liquidation that follows a restart would be refused as a naked short — which is
    how a stop-trading decision becomes an unintended overnight hold."""
    path = tmp_path / "orders.jsonl"
    journal = JsonlOrderJournal(path, broker_name="simulated")
    live = SimulatedBroker(initial_cash=10_000_000, journal=journal)
    live.submit(_order("a1", quantity=100), _quote(ask=1000.0, ask_size=100))

    orders, fills = JsonlOrderJournal(path).replay()
    restored = SimulatedBroker.restore_from(orders, fills, initial_cash=10_000_000, journal=journal)

    exit_order = restored.submit(
        _order("x1", side=Side.SELL, quantity=100, limit_price=900.0),
        _quote(bid=990.0, ask=991.0, bid_size=100),
    )

    assert exit_order.state is OrderState.FILLED
    assert restored.positions() == {}
