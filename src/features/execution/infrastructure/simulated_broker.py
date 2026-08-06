"""In-process broker simulator.

Ported in shape from `scanner/live/order_adapter.py`, which is the one piece of that
codebase whose abstraction held up: a partial-fill state machine driven by quotes,
with positions derived from fills rather than tracked alongside them. The state
machine here is the same machine. Four things were changed, and each one closes a
hole that cost something in the original system:

**A book that reports no size fills nothing.** The original read `ask_size == 0` as
"size not reported" and filled the entire remaining quantity. That is the fill
fantasy this repository documents: a simulator that fills against liquidity nobody
offered produces a backtest whose fill rate is 100% and whose live fill rate was 12%.
Here zero size means zero shares, matching `Quote.is_locked`.

**One quote's size is one pool.** Two resting orders on the same side used to each
receive `min(remaining, ask_size)` from the same quote, so 100 offered shares filled
200. The pool is now shared and drained within a single `on_quote` call.

**Committed quantity and committed cash are reserved.** Validation used to compare
each order against the *unencumbered* book, so two sell orders could each pass
against one holding and together go short, and two buy orders could each pass against
the same cash. Working orders now reserve what they will consume.

**Nothing is ever guessed.** `positions()` raises when the book has not reconciled
instead of answering `{}`. An empty dict reads as "flat", and acting on a false flat
means re-entering a position that is already open — the exact failure that made a
restart in the imitated system dangerous rather than merely inconvenient.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import datetime

from src.features.execution.domain.entities.order import (
    Fill,
    Order,
    OrderState,
    Position,
)
from src.features.execution.domain.repositories.broker import (
    Broker,
    BrokerUnavailable,
    OrderJournal,
    OrderRejected,
)
from src.features.market_data.domain.entities.bar import Quote
from src.features.operations.infrastructure.controls import FileKillSwitch
from src.features.strategy.domain.entities.signal import Side
from src.shared.domain import clock

_EPSILON = 1e-9
"""Share and currency comparison tolerance.

Quantities arrive as floats because the domain model carries them as floats, and
`0.1 + 0.2 > 0.3` would otherwise reject a perfectly affordable order.
"""


def marketable_price(side: Side, limit_price: float | None, quote: Quote) -> float | None:
    """Price at which this order would transact against `quote`, or None.

    None rather than 0.0 for "would not transact": the original returned 0.0 and every
    caller had to remember that 0.0 was a sentinel rather than a price. A locked book
    legitimately reports an ask of zero, so the sentinel and the datum collided.
    """
    if side is Side.BUY:
        if quote.ask <= 0:
            return None
        if limit_price is None or limit_price >= quote.ask:
            return quote.ask
        return None

    if quote.bid <= 0:
        return None
    if limit_price is None or limit_price <= quote.bid:
        return quote.bid
    return None


def available_size(side: Side, quote: Quote) -> float:
    """Shares the book is actually offering to this side of the order."""
    return quote.ask_size if side is Side.BUY else quote.bid_size


class SimulatedBroker(Broker):
    """Deterministic broker for backtests, dry runs, and restart drills.

    Holds no network handle and reads no credential, so it is the implementation the
    test suite and the shipped demonstration both run against.
    """

    def __init__(
        self,
        *,
        initial_cash: float = 0.0,
        enforce_cash: bool = True,
        kill_switch: FileKillSwitch | None = None,
        journal: OrderJournal | None = None,
    ) -> None:
        """
        :param enforce_cash: defaults to True, unlike the original, which defaulted to
            False. A simulator that can spend money it does not have reports a return
            on capital it never had, and the number looks entirely ordinary.
        :param journal: when supplied, every order transition and every fill is
            recorded as it happens. Pass this **or** journal the returned objects
            yourself, never both — `record_fill` is not idempotent and a fill written
            twice is a doubled position on the next replay.
        """
        self._cash = initial_cash
        self._enforce_cash = enforce_cash
        self._kill_switch = kill_switch
        self._journal = journal

        self._orders: dict[str, Order] = {}
        self._fills: list[Fill] = []
        self._positions: dict[str, Position] = {}
        self._keys: dict[str, str] = {}
        self._problems: list[str] = []

    # ── identity ─────────────────────────────────────────────────────────────
    @property
    def name(self) -> str:
        return "simulated"

    @property
    def places_real_orders(self) -> bool:
        """False, and a test asserts it.

        The runtime banner reads this to tell a reviewer whether the thing they just
        started can move money. A banner that is derived from a constant somewhere
        else eventually disagrees with reality; a banner derived from the object doing
        the work cannot.
        """
        return False

    # ── observable state ─────────────────────────────────────────────────────
    @property
    def cash(self) -> float:
        return self._cash

    @property
    def state_known(self) -> bool:
        """Whether the reconstructed book can be trusted.

        False after a replay that did not add up. Kept as a property rather than an
        exception at restore time so an operator can still inspect what was recovered;
        the refusal lives at `positions()` and `submit()`, which are the paths where
        acting on a wrong book costs money.
        """
        return not self._problems

    @property
    def problems(self) -> tuple[str, ...]:
        """Why the book is not trusted. Empty when it is."""
        return tuple(self._problems)

    def mark_state_unknown(self, reason: str) -> None:
        """Declare the book unreconciled.

        Exists so a caller that discovers a discrepancy elsewhere — a venue position
        report disagreeing with the journal, say — can force the broker to refuse
        rather than keep answering confidently.
        """
        if not reason:
            raise ValueError("mark_state_unknown requires a reason; a flag with no reason is unactionable")
        self._problems.append(reason)

    def positions(self) -> dict[str, Position]:
        """Net holdings, flat symbols omitted.

        A symbol that has gone flat is not a holding, and leaving a zero row in makes
        `symbol in positions()` true for something you do not own.

        :raise BrokerUnavailable: when the book has not reconciled.
        """
        if self._problems:
            raise BrokerUnavailable(
                "position state is not reconciled: " + "; ".join(self._problems)
            )
        return {
            symbol: position
            for symbol, position in self._positions.items()
            if not position.is_flat
        }

    def working_orders(self) -> list[Order]:
        """Orders still able to receive fills.

        Answers from the journalled order records even when the book is unreconciled:
        what was submitted is the part we are most sure of, and an operator dealing
        with a bad restart needs to see the live exposure before anything else.
        """
        return [order for order in self._orders.values() if order.state.is_working]

    def all_orders(self) -> list[Order]:
        return list(self._orders.values())

    def fills(self) -> list[Fill]:
        return list(self._fills)

    def get_order(self, order_id: str) -> Order:
        """
        :raise OrderRejected: for an unknown id. Never a synthesised placeholder — a
            caller that receives a plausible object for an order the broker never saw
            will act on it.
        """
        try:
            return self._orders[order_id]
        except KeyError as exc:
            raise OrderRejected(f"unknown order_id: {order_id}") from exc

    # ── submission ───────────────────────────────────────────────────────────
    def submit(self, order: Order, quote: Quote | None = None) -> Order:
        """Accept, refuse, or immediately fill an order.

        The kill switch is consulted first and its exception is deliberately allowed
        to escape: submission is the single path the switch exists to stop, and a
        broker that caught it here would turn the operator's halt into a log line.

        When `quote` is supplied only *this* order is filled against it. Resting
        orders are advanced by `on_quote`, which is where they compete for size.

        :raise KillSwitchEngaged: when new risk is halted.
        :raise BrokerUnavailable: when the book has not reconciled — placing new risk
            on top of a position you cannot state is how a bad restart compounds.
        :raise OrderRejected: for caller mistakes that are not market outcomes: a
            duplicate order id, a duplicate idempotency key, a resubmitted order, or a
            quote for a different instrument.
        """
        if self._kill_switch is not None:
            self._kill_switch.guard_submission()

        if self._problems:
            raise BrokerUnavailable(
                "refusing to submit against an unreconciled book: " + "; ".join(self._problems)
            )

        if order.order_id in self._orders:
            raise OrderRejected(f"duplicate order_id: {order.order_id}")

        if order.state is not OrderState.PENDING:
            raise OrderRejected(
                f"order {order.order_id} was already submitted (state {order.state.value})"
            )

        if order.idempotency_key and order.idempotency_key in self._keys:
            raise OrderRejected(
                f"duplicate idempotency_key {order.idempotency_key!r} "
                f"(already placed as {self._keys[order.idempotency_key]})"
            )

        if quote is not None and quote.symbol != order.symbol:
            raise OrderRejected(
                f"quote is for {quote.symbol}, order is for {order.symbol}"
            )

        reason = self._rejection_reason(order)
        if reason:
            # A refusal is an ordinary outcome and comes back as a REJECTED order
            # rather than an exception, so the caller journals it like any other
            # transition. An unjournalled rejection is invisible at 3am.
            rejected = order.model_copy(update={"state": OrderState.REJECTED, "reason": reason})
            self._orders[rejected.order_id] = rejected
            self._record_order(rejected)
            return rejected

        accepted = order.model_copy(update={"state": OrderState.OPEN})
        self._orders[accepted.order_id] = accepted
        if accepted.idempotency_key:
            # Registered only now, after acceptance: a rejected order placed no risk,
            # so burning its key would turn a momentary cash shortfall into a
            # permanent refusal to retry the same intent for the rest of the day.
            self._keys[accepted.idempotency_key] = accepted.order_id
        self._record_order(accepted)

        if quote is not None:
            self._advance(accepted, quote, available_size(accepted.side, quote))

        return self._orders[accepted.order_id]

    def cancel(self, order_id: str) -> Order:
        """Cancel a working order.

        Deliberately does **not** consult the kill switch. A switch that also froze
        cancellation would strand open positions with no way out, converting a "stop
        trading" decision into an unhedged hold — see `controls.py`, where the same
        asymmetry is stated.

        :raise OrderRejected: for an unknown id.
        """
        order = self.get_order(order_id)
        if order.state.is_terminal:
            return order
        cancelled = order.model_copy(update={"state": OrderState.CANCELLED})
        self._orders[order_id] = cancelled
        self._record_order(cancelled)
        return cancelled

    # ── fills ────────────────────────────────────────────────────────────────
    def on_quote(self, quote: Quote) -> list[Fill]:
        """Advance every resting order in this instrument against a new book.

        The two size pools are drained as orders consume them, so a quote offering 100
        shares cannot fill two orders of 100. Orders are served in submission order,
        which is the closest a simulator gets to the venue's time priority.
        """
        pools = {Side.BUY: quote.ask_size, Side.SELL: quote.bid_size}
        produced: list[Fill] = []

        for order in list(self._orders.values()):
            if order.symbol != quote.symbol or not order.state.is_working:
                continue
            fill, pools[order.side] = self._advance(order, quote, pools[order.side])
            if fill is not None:
                produced.append(fill)

        return produced

    def apply_fill(
        self,
        order_id: str,
        quantity: float,
        price: float,
        timestamp: datetime | None = None,
    ) -> Fill:
        """Apply one execution to an order, its position, and cash.

        The single place an order's quantity moves. A real adapter receives async
        execution reports and the venue occasionally sends one twice; routing every
        fill through one guarded method is what makes the second report a raised error
        instead of a silently doubled position.

        :raise OrderRejected: if the order is unknown, already terminal, or the fill
            would exceed the remaining quantity. Overfill is refused rather than
            clamped — clamping produces a plausible book and hides the defect that
            generated the extra shares.
        """
        order = self.get_order(order_id)

        if not order.state.is_working:
            raise OrderRejected(
                f"order {order_id} is {order.state.value} and cannot take further fills"
            )
        if not math.isfinite(quantity) or quantity <= 0:
            raise OrderRejected(f"fill quantity must be positive and finite: {quantity}")
        if not math.isfinite(price) or price <= 0:
            raise OrderRejected(f"fill price must be positive and finite: {price}")
        if quantity > order.remaining_quantity + _EPSILON:
            raise OrderRejected(
                f"overfill on {order_id}: {quantity} against {order.remaining_quantity} remaining"
            )

        at = clock.now() if timestamp is None else timestamp
        filled_quantity = order.filled_quantity + quantity
        gross = order.average_fill_price * order.filled_quantity + price * quantity
        state = (
            OrderState.FILLED
            if filled_quantity >= order.quantity - _EPSILON
            else OrderState.PARTIALLY_FILLED
        )

        updated = order.model_copy(
            update={
                "state": state,
                "filled_quantity": filled_quantity,
                "average_fill_price": gross / filled_quantity,
            }
        )
        fill = Fill(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=quantity,
            price=price,
            timestamp=at,
        )

        self._orders[order_id] = updated
        self._fills.append(fill)
        self._post_to_book(fill)
        self._record_order(updated)
        self._record_fill(fill)
        return fill

    def _advance(self, order: Order, quote: Quote, pool: float) -> tuple[Fill | None, float]:
        """Fill one order against one book, returning the fill and the size left."""
        price = marketable_price(order.side, order.limit_price, quote)
        if price is None:
            return None, pool

        quantity = min(order.remaining_quantity, pool)
        if quantity <= _EPSILON:
            # Zero offered size means zero shares, not unlimited shares. The original
            # read it as unlimited and filled the whole order against an empty book.
            return None, pool

        fill = self.apply_fill(order.order_id, quantity, price, quote.timestamp)
        return fill, pool - quantity

    def _post_to_book(self, fill: Fill) -> None:
        existing = self._positions.get(fill.symbol) or Position(symbol=fill.symbol)
        signed = fill.quantity if fill.side is Side.BUY else -fill.quantity
        new_quantity = existing.quantity + signed

        if fill.side is Side.BUY:
            cost = existing.average_price * existing.quantity + fill.price * fill.quantity
            average_price = cost / new_quantity if new_quantity > 0 else 0.0
            self._cash -= fill.notional
        else:
            # Selling does not change the cost basis of what remains; realised profit
            # and loss is computed from the journal, not carried on the position.
            average_price = existing.average_price if new_quantity > 0 else 0.0
            self._cash += fill.notional

        self._positions[fill.symbol] = Position(
            symbol=fill.symbol,
            quantity=new_quantity,
            average_price=max(0.0, average_price),
        )

    # ── validation ───────────────────────────────────────────────────────────
    def _rejection_reason(self, order: Order) -> str:
        """Why this order cannot be accepted, or an empty string."""
        if not math.isfinite(order.quantity) or order.quantity <= 0:
            return f"quantity must be positive and finite: {order.quantity}"

        if abs(order.quantity - round(order.quantity)) > _EPSILON:
            # KRX has no fractional shares. A fractional quantity that the simulator
            # accepts and the venue silently truncates makes every fill-rate number
            # computed against it wrong by an amount nobody can see.
            return f"KRX trades whole shares; {order.quantity} is fractional"

        if order.limit_price is not None and (
            not math.isfinite(order.limit_price) or order.limit_price <= 0
        ):
            return f"limit_price must be positive and finite: {order.limit_price}"

        if order.side is Side.SELL:
            held = self._positions.get(order.symbol)
            unencumbered = (held.quantity if held else 0.0) - self._committed_shares(order.symbol)
            if order.quantity > unencumbered + _EPSILON:
                return (
                    f"naked short refused: {order.quantity} requested, "
                    f"{unencumbered} unencumbered in {order.symbol}"
                )

        if self._enforce_cash and order.side is Side.BUY:
            if order.limit_price is None:
                # An unpriced buy cannot be checked for affordability, and checking it
                # after the fill means checking it after the money is gone.
                return "market buy cannot be affordability-checked; supply a limit price"
            required = order.quantity * order.limit_price
            spendable = self._cash - self._committed_cash()
            if required > spendable + _EPSILON:
                return f"insufficient cash: {required:.2f} required, {spendable:.2f} available"

        return ""

    def _committed_shares(self, symbol: str) -> float:
        """Shares already promised to working sell orders in this instrument."""
        return sum(
            order.remaining_quantity
            for order in self._orders.values()
            if order.symbol == symbol
            and order.side is Side.SELL
            and order.state.is_working
        )

    def _committed_cash(self) -> float:
        """Cash already promised to working buy orders at their limit prices."""
        return sum(
            order.remaining_quantity * order.limit_price
            for order in self._orders.values()
            if order.side is Side.BUY
            and order.state.is_working
            and order.limit_price is not None
        )

    # ── recovery ─────────────────────────────────────────────────────────────
    @classmethod
    def restore_from(
        cls,
        orders: Iterable[Order],
        fills: Iterable[Fill],
        *,
        initial_cash: float = 0.0,
        enforce_cash: bool = True,
        kill_switch: FileKillSwitch | None = None,
        journal: OrderJournal | None = None,
    ) -> SimulatedBroker:
        """Rebuild a broker from a journal replay.

        The method the imitated system did not have. It journalled every order and
        every fill and never read either back, so a restart lost every open order and
        every position with full knowledge of both sitting on disk.

        :param initial_cash: cash at the *start* of the replayed history, not now.
            The fills are re-applied to it, so passing the current balance would
            deduct every purchase a second time.
        """
        broker = cls(
            initial_cash=initial_cash,
            enforce_cash=enforce_cash,
            kill_switch=kill_switch,
            journal=journal,
        )
        broker.adopt(orders, fills)
        return broker

    def adopt(self, orders: Iterable[Order], fills: Iterable[Fill]) -> None:
        """Load replayed records and check that they agree with each other.

        Reconciliation is the point. Two independent accounts of the same history are
        recorded — each order's `filled_quantity`, and the fills themselves — and if
        they disagree the broker is loaded but flagged, so `positions()` refuses
        instead of reporting a book assembled from records that contradict.
        """
        for order in orders:
            self._orders[order.order_id] = order
            if order.idempotency_key and order.state is not OrderState.REJECTED:
                self._keys[order.idempotency_key] = order.order_id

        replayed: dict[str, float] = {}
        for fill in fills:
            if fill.order_id not in self._orders:
                self._problems.append(
                    f"fill references order {fill.order_id}, which the journal never recorded"
                )
            self._fills.append(fill)
            self._post_to_book(fill)
            replayed[fill.order_id] = replayed.get(fill.order_id, 0.0) + fill.quantity

        for order in self._orders.values():
            seen = replayed.get(order.order_id, 0.0)
            if abs(seen - order.filled_quantity) > 1e-6:
                self._problems.append(
                    f"order {order.order_id} records filled_quantity {order.filled_quantity} "
                    f"but the fills sum to {seen}"
                )

        for symbol, position in self._positions.items():
            if position.quantity < -_EPSILON:
                self._problems.append(
                    f"replay produced a short position in {symbol} ({position.quantity}), "
                    "which this broker refuses to open"
                )

    # ── journalling ──────────────────────────────────────────────────────────
    def _record_order(self, order: Order) -> None:
        if self._journal is not None:
            self._journal.record_order(order)

    def _record_fill(self, fill: Fill) -> None:
        if self._journal is not None:
            self._journal.record_fill(fill)
