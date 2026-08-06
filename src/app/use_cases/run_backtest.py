"""Replay every session in the panel and judge the result properly.

This use case exists because `run_session` cannot be trusted to answer the question
anyone actually asks. One session is one observation. It will produce a number, and
the number will look like a result, and it will be noise.

So the judgement here goes through the same `cluster_by_session` that the research
layer uses — and that function **raises** when handed a single session rather than
returning a t-statistic of zero or None. Wiring the running system into that guard,
instead of writing a fresh average here, is the point: the discipline the repository
argues for in prose is the code path the binary actually takes.

The correction it enforces is the most consequential one in this project's history.
A gap-down cell read t = 12.7 per event and t = −0.92 once clustered on sessions,
because 1,220 events lived on 304 days with one day carrying 62 of them. Effective
sample size is the session count.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from pydantic import BaseModel, ConfigDict

from src.app.use_cases.run_session import SessionReport, run_session
from src.app.wiring import System
from src.features.execution.domain.entities.order import Fill, Order
from src.features.execution.domain.repositories.broker import OrderJournal
from src.features.execution.infrastructure.simulated_broker import SimulatedBroker


class _NullJournal(OrderJournal):
    """Discards records. Used only for backtest sessions.

    A backtest writes nothing durable on purpose: its output is the report, and a
    research run that appends to the live order journal would corrupt the one file
    the running system rebuilds its positions from.
    """

    def record_order(self, order: Order) -> None:
        return None

    def record_fill(self, fill: Fill) -> None:
        return None

    def replay(self) -> tuple[list[Order], list[Fill]]:
        return ([], [])
from src.features.falsification.domain.services.day_clustering import (
    cluster_by_session,
    drop_worst_session,
)
from src.shared.domain import clock
from src.shared.domain.entities.market import ROUND_TRIP_COST_PCT
from src.shared.domain.errors import InsufficientData


class BacktestReport(BaseModel):
    """Every session, judged on the session count rather than the trade count."""

    model_config = ConfigDict(frozen=True)

    strategy: str
    verdict: str
    expected_to_lose: bool

    sessions: int
    total_fills: int

    mean_net_pct: float
    day_clustered_t: float | None
    positive_sessions: int

    without_best_session_mean: float | None = None
    without_best_session_t: float | None = None
    best_session: str | None = None

    verdict_line: str = ""

    def summary(self) -> str:
        t = f"{self.day_clustered_t:+.2f}" if self.day_clustered_t is not None else "n/a"
        lines = [
            f"strategy         {self.strategy}  [{self.verdict}]",
            f"sessions         {self.sessions}          <- the effective sample size",
            f"fills            {self.total_fills}",
            f"mean net         {self.mean_net_pct:+.3f}%  (after {ROUND_TRIP_COST_PCT}% round trip)",
            f"day-clustered t  {t}",
            f"positive days    {self.positive_sessions}/{self.sessions}",
        ]
        if self.without_best_session_t is not None:
            lines.append(
                f"drop best day    {self.without_best_session_mean:+.3f}%  "
                f"t {self.without_best_session_t:+.2f}   (removed {self.best_session})"
            )
        lines.append("")
        lines.append(self.verdict_line)
        return "\n".join(lines)


def _judge(t_stat: float | None, sessions: int, expected_to_lose: bool) -> str:
    """Turn the statistics into a sentence that does not overclaim.

    The bar is |t| >= 2 *and* enough sessions to have measured anything. Neither is
    sufficient alone, and a repository that reports one without the other is doing
    the thing it criticises.
    """
    if sessions < 5:
        return (
            f"NOT JUDGEABLE — {sessions} sessions. This is not a weak result, it is an\n"
            f"absent one. No statistic computed on this sample would mean anything."
        )
    if t_stat is None:
        return "NOT JUDGEABLE — zero dispersion across sessions; a degenerate sample."
    if t_stat <= -2:
        return (
            "LOSES, and significantly so. For the shipped default this is the\n"
            "documented outcome: the rule was selected on a training fold and ranked\n"
            "8th of 12 out of sample. See docs/postmortems/04-train-selection-hour-scan.md"
        )
    if abs(t_stat) < 2:
        return (
            "INDISTINGUISHABLE FROM NOISE at |t| < 2. Which is the honest reading of\n"
            "almost everything this project tested, and the reason its graveyard is\n"
            "long and its survivors section is one page."
        )
    return (
        "Positive at |t| >= 2 on synthetic data with no embedded edge — so this is a\n"
        "false positive by construction, and a useful reminder that a t-statistic\n"
        "computed on a small sample of a random process crosses 2 routinely."
    )


def run_backtest(system: System, max_sessions: int | None = None) -> BacktestReport:
    """Replay every session in the panel and cluster the result on sessions.

    :param max_sessions: cap for a quick run; None replays everything available.
    :raise MarketDataUnavailable: if the panel cannot be read.
    :raise InsufficientData: never — a too-small sample is reported as NOT JUDGEABLE
        rather than raised, because "we could not measure" is a result the caller
        needs to see rather than an exception it has to catch.
    """
    symbols = system.source.available_symbols()
    interval = system.config.interval_minutes
    days: set[date] = set()
    for symbol in symbols:
        for bar in system.source.fetch_bars(symbol, interval):
            days.add(bar.timestamp.astimezone(clock.MARKET_TZ).date())

    ordered = sorted(days)
    if max_sessions:
        ordered = ordered[-max_sessions:]

    reports: list[SessionReport] = []
    for day in ordered:
        moment = clock.session_bounds(day)[0]
        # Each session gets a fresh broker and a throwaway journal.
        #
        # A backtest is a research operation and must not share state with the live
        # book. Running it against the persistent journal collides on order ids the
        # moment a day is replayed twice — which is the state-recovery machinery
        # working correctly, and precisely why the two must be separated rather than
        # having the ids made unique to paper over it.
        isolated = replace(
            system,
            journal=_NullJournal(),
            broker=SimulatedBroker(
                initial_cash=system.config.initial_cash,
                kill_switch=None,
                journal=None,
            ),
        )
        try:
            reports.append(run_session(isolated, session_day=moment))
        except ValueError:
            continue  # no bars for that calendar day

    nets = [r.net_return_pct for r in reports]
    session_keys = [r.session_day for r in reports]
    spec = system.strategy.spec

    clustered = None
    dropped_mean = dropped_t = None
    best_day = None
    try:
        clustered = cluster_by_session(nets, session_keys)
    except InsufficientData:
        pass

    if clustered is not None:
        try:
            best_day, dropped = drop_worst_session(nets, session_keys)
            dropped_mean, dropped_t = dropped.mean, dropped.t
        except InsufficientData:
            pass

    mean = clustered.mean if clustered else (sum(nets) / len(nets) if nets else 0.0)
    t_stat = clustered.t if clustered else None

    return BacktestReport(
        strategy=spec.name,
        verdict=spec.verdict,
        expected_to_lose=spec.is_expected_to_lose,
        sessions=len(reports),
        total_fills=sum(r.fills for r in reports),
        mean_net_pct=round(mean, 4),
        day_clustered_t=round(t_stat, 4) if t_stat is not None else None,
        positive_sessions=clustered.positive_sessions if clustered else sum(1 for n in nets if n > 0),
        without_best_session_mean=round(dropped_mean, 4) if dropped_mean is not None else None,
        without_best_session_t=round(dropped_t, 4) if dropped_t is not None else None,
        best_session=str(best_day) if best_day else None,
        verdict_line=_judge(t_stat, len(reports), spec.is_expected_to_lose),
    )
