"""Run one trading session end to end.

This is the use case that makes the repository a system rather than a library. It
walks a session's bars in order, and at each step it does exactly what a live session
would do, in the same order, through the same interfaces.

Two properties are load-bearing and both are enforced here rather than trusted to the
strategy:

**Point-in-time slicing happens once, at the top.** The strategy is handed bars
already truncated to the decision instant and has no way to reach further. Trusting
every strategy to slice correctly is how look-ahead gets in: a strategy that peeks
produces a beautiful result and no error, so the check has to live where the data is
cut, not where it is consumed.

**Execution is strictly after the signal.** A signal decided on the bar closing at T
is filled against the bar that opens after T. The gap is not conservatism for its own
sake — a strategy in this project's graveyard reported t = 3.71 by entering at a price
its own signal needed in order to exist, and re-running it on the first genuinely
executable leg left t = 1.34.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict

from src.app.wiring import System
from src.features.execution.domain.entities.order import Order, OrderState
from src.features.market_data.domain.entities.bar import Bar, Quote
from src.features.strategy.domain.entities.signal import Side, Signal
from src.shared.domain import clock
from src.shared.domain.entities.market import ROUND_TRIP_COST_PCT


class SessionReport(BaseModel):
    """What one session produced. Deliberately reports refusals alongside results."""

    model_config = ConfigDict(frozen=True)

    session_day: str
    strategy: str
    verdict: str
    expected_to_lose: bool

    evaluations: int
    signals: int
    orders_submitted: int
    orders_rejected: int
    fills: int

    gross_return_pct: float
    net_return_pct: float
    """After a flat round-trip friction of 0.38%, applied once per completed
    round trip. Never reported gross-only: every strategy in the graveyard that
    looked tradable gross is in the graveyard because of this subtraction."""

    stranded_positions: int = 0
    """Positions the exit leg could not fully close.

    Reported as a first-class number rather than buried, because getting in and not
    being able to get out is this project's most replicated finding."""

    unfilled_signals: int
    """Signals that produced no fill. Tracked because the difference between what a
    strategy wanted and what it got is, in this project's experience, where the edge
    usually went."""

    notes: tuple[str, ...] = ()

    def summary(self) -> str:
        lines = [
            f"session        {self.session_day}",
            f"strategy       {self.strategy}  [{self.verdict}]",
            f"evaluations    {self.evaluations}",
            f"signals        {self.signals}  (unfilled: {self.unfilled_signals})",
            f"orders         {self.orders_submitted} submitted, {self.orders_rejected} rejected",
            f"stranded       {self.stranded_positions} position(s) the exit could not close",
            f"fills          {self.fills}",
            f"gross          {self.gross_return_pct:+.3f}%",
            f"net            {self.net_return_pct:+.3f}%   (after {ROUND_TRIP_COST_PCT}% round trip)",
        ]
        if self.expected_to_lose:
            lines.append("")
            lines.append(
                "This strategy is documented as refuted. A negative result here is the\n"
                "expected outcome and is the point of shipping it — see\n"
                "docs/postmortems/04-train-selection-hour-scan.md"
            )
        for note in self.notes:
            lines.append(f"note           {note}")
        return "\n".join(lines)


def _quote_from(bar: Bar) -> Quote:
    """A quote standing in for a book we do not have.

    The synthetic panel carries bars, not depth. Rather than invent a spread that
    would flatter fills, the bar's open is used for both sides and the offered size is
    capped at a fraction of the bar's volume — so an order larger than the market can
    absorb goes partially unfilled instead of magically clearing.

    This is a simplification and it is stated as one: a real fill model needs real
    depth, which is exactly the data this repository cannot redistribute.
    """
    absorbable = bar.volume * 0.10
    return Quote(
        symbol=bar.symbol,
        timestamp=bar.timestamp,
        bid=bar.open,
        ask=bar.open,
        bid_size=absorbable,
        ask_size=absorbable,
    )


def run_session(
    system: System,
    session_day: datetime | None = None,
    step_minutes: int | None = None,
) -> SessionReport:
    """Walk one session, evaluating and executing at each step.

    :param session_day: any moment inside the day to replay. Defaults to the newest
        day present in the sample panel, so `make run` works with no arguments.
    :param step_minutes: evaluation cadence; defaults to the bar interval.
    :raise MarketDataUnavailable: if bars cannot be loaded.
    :raise StrategyError: if the strategy fails. Not caught — a strategy that crashes
        is an incident, and reporting it as a quiet session would hide it.
    """
    config = system.config
    interval = config.interval_minutes
    step = step_minutes or config.evaluation_step_minutes

    symbols = system.source.available_symbols()
    bars_by_symbol: dict[str, list[Bar]] = {}
    for symbol in symbols:
        loaded = system.source.fetch_bars(symbol, interval)
        if loaded:
            bars_by_symbol[symbol] = loaded

    if not bars_by_symbol:
        raise ValueError("sample panel contains no bars at the configured interval")

    system.store.append_bars([b for bars in bars_by_symbol.values() for b in bars])

    all_times = sorted({b.timestamp for bars in bars_by_symbol.values() for b in bars})
    if session_day is None:
        target = all_times[-1].astimezone(clock.MARKET_TZ).date()
    else:
        target = session_day.astimezone(clock.MARKET_TZ).date()
    session_times = [t for t in all_times if t.astimezone(clock.MARKET_TZ).date() == target]
    if not session_times:
        raise ValueError(f"no bars for {target.isoformat()} in the sample panel")

    day_key = target.isoformat()
    evaluations = signals_emitted = submitted = rejected = fills = 0
    unfilled = 0
    entries: dict[str, tuple[float, float]] = {}   # symbol -> (qty, avg price)
    stranded: dict[str, float] = {}               # symbol -> shares that would not exit
    realised_gross = 0.0
    round_trips = 0
    notes: list[str] = []

    cadence = max(1, step // interval)
    for index, as_of in enumerate(session_times):
        if index % cadence:
            continue

        # ── the single point-in-time cut ──────────────────────────────────────
        view = {
            symbol: [b for b in bars if b.timestamp <= as_of]
            for symbol, bars in bars_by_symbol.items()
        }
        view = {s: b for s, b in view.items() if b}
        if not view:
            continue
        evaluations += 1

        emitted = system.strategy.evaluate(as_of, view, config.max_signals_per_evaluation)
        signals_emitted += len(emitted)

        for signal in emitted:
            # One entry per symbol per session. The broker enforces this anyway via
            # the idempotency key — it refused the second submission the first time
            # this loop was written without the check, which is the contract doing
            # its job. Checking here as well keeps the refusal out of the rejection
            # count, where it would read as the broker declining a legitimate order.
            if signal.symbol in entries:
                continue

            future = [b for b in bars_by_symbol[signal.symbol] if b.timestamp > as_of]
            if not future:
                unfilled += 1
                continue
            execution_bar = future[0]
            quote = _quote_from(execution_bar)

            quantity = int(config.order_notional // execution_bar.open)
            if quantity <= 0:
                unfilled += 1
                continue

            order = Order(
                order_id=f"{day_key}-{signal.symbol}-{index}",
                symbol=signal.symbol,
                side=signal.side,
                quantity=float(quantity),
                limit_price=execution_bar.open * 1.003,
                created_at=as_of,
                reason=signal.reason,
                idempotency_key=f"{day_key}|{signal.symbol}|entry",
                state=OrderState.PENDING,
            )
            placed = system.broker.submit(order, quote)
            submitted += 1
            if placed.state is OrderState.REJECTED:
                rejected += 1
                continue
            if placed.filled_quantity <= 0:
                unfilled += 1
                continue
            fills += 1
            entries[signal.symbol] = (placed.filled_quantity, placed.average_fill_price)

    # ── liquidate through the broker, not on paper ────────────────────────────
    #
    # The exit leg goes through `submit` like the entry did. Computing the exit
    # arithmetically here and skipping the broker would be simpler and would be a
    # lie: cash would never return, position state would drift from the journal, and
    # the run would silently stop trading after the first session because it had
    # spent everything. That is exactly what happened the first time this was
    # written, and one session out of twenty traded.
    for symbol, (quantity, entry_price) in entries.items():
        window = [b for b in bars_by_symbol[symbol] if b.timestamp <= session_times[-1]]
        if not window:
            continue
        last_bar = window[-1]
        exit_order = Order(
            order_id=f"{day_key}-{symbol}-exit",
            symbol=symbol,
            side=Side.SELL,
            quantity=quantity,
            limit_price=last_bar.close * 0.997,
            created_at=last_bar.timestamp,
            reason="session close",
            idempotency_key=f"{day_key}|{symbol}|exit",
            state=OrderState.PENDING,
        )
        closed = system.broker.submit(exit_order, _quote_from(last_bar))
        if closed.filled_quantity <= 0:
            stranded[symbol] = quantity
            notes.append(f"{symbol}: exit did not fill at all — position held overnight")
            continue
        if closed.filled_quantity < quantity - 1e-9:
            # Partial liquidation. Reported rather than rounded away, because "we got
            # in and could not fully get out" is the central finding of this project
            # and a session runner that silently completes the exit on paper would
            # reproduce exactly the fill fantasy the graveyard is full of.
            stranded[symbol] = quantity - closed.filled_quantity
        realised_gross += (closed.average_fill_price / entry_price - 1) * 100
        round_trips += 1

    gross = realised_gross / round_trips if round_trips else 0.0
    net = gross - ROUND_TRIP_COST_PCT if round_trips else 0.0

    if unfilled:
        notes.append(
            f"{unfilled} signals produced no fill — the gap between intent and "
            f"execution is where this project's edges usually went"
        )
    if stranded:
        notes.append(
            f"{len(stranded)} position(s) could not be fully liquidated at the close. "
            f"This is the finding, not a bug in the runner: fillability and edge are "
            f"mutually exclusive, and the exit leg is where that bites"
        )

    spec = system.strategy.spec
    return SessionReport(
        session_day=day_key,
        strategy=spec.name,
        verdict=spec.verdict,
        expected_to_lose=spec.is_expected_to_lose,
        evaluations=evaluations,
        signals=signals_emitted,
        orders_submitted=submitted,
        orders_rejected=rejected,
        fills=fills,
        gross_return_pct=round(gross, 4),
        net_return_pct=round(net, 4),
        unfilled_signals=unfilled,
        stranded_positions=len(stranded),
        notes=tuple(notes),
    )
