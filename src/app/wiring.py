"""Composition root — the one place concrete classes are chosen.

Every other module in `src/` names an interface. This file is the only one that knows
which implementation is bound, which is what makes the claim "the strategy is a
plugin" checkable rather than rhetorical: a reader can see the entire set of
substitutable parts in one screen.

It is also where a restart becomes survivable. `System.restore()` replays the journal
before anything else touches the broker, so working orders and positions are rebuilt
from durable evidence rather than starting empty. The system this imitates skipped
that step and lost every open position on each deploy, with the journal that could
have rebuilt them sitting unread one directory away.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.app.config import AppConfig
from src.features.execution.domain.repositories.broker import Broker, OrderJournal
from src.features.execution.infrastructure.jsonl_journal import JsonlOrderJournal
from src.features.execution.infrastructure.simulated_broker import SimulatedBroker
from src.features.market_data.domain.repositories.market_data import (
    BarStore,
    MarketDataSource,
)
from src.features.market_data.infrastructure.parquet_store import ParquetBarStore
from src.features.market_data.infrastructure.replay_source import ReplayMarketDataSource
from src.features.operations.domain.entities.trading_mode import TradingMode
from src.features.operations.infrastructure.controls import FileKillSwitch, FileTradingMode
from src.features.operations.infrastructure.logging import StructuredLogger
from src.features.strategy.domain.repositories.strategy import Strategy
from src.features.strategy.infrastructure.registry import get_strategy


@dataclass
class System:
    """Everything wired together, with its restore step already run."""

    config: AppConfig
    logger: StructuredLogger
    mode: TradingMode
    kill_switch: FileKillSwitch
    source: MarketDataSource
    store: BarStore
    strategy: Strategy
    journal: OrderJournal
    broker: Broker
    restored_orders: int = 0
    restored_fills: int = 0

    def banner(self) -> str:
        """What the runtime prints before it does anything.

        Three facts a reviewer needs before trusting any number that follows: which
        strategy is loaded and whether it is expected to lose, whether the broker can
        move real money, and how much prior state was recovered.
        """
        lines = [
            f"mode           {self.mode.value}",
            f"broker         {self.broker.name}  (real orders: {self.broker.places_real_orders})",
            f"market data    {self.source.name}",
            f"strategy       {self.strategy.describe()}",
        ]
        if self.restored_orders or self.restored_fills:
            lines.append(
                f"restored       {self.restored_orders} orders, "
                f"{self.restored_fills} fills from the journal"
            )
        if self.kill_switch.engaged:
            lines.append(f"KILL SWITCH    ENGAGED — {self.kill_switch.reason()}")
        return "\n".join(lines)


def build(config: AppConfig | None = None) -> System:
    """Assemble the system and restore its state.

    :raise MarketDataUnavailable: if the configured data source cannot serve.
    :raise UnknownStrategy: if the configured strategy key is not registered.
    :raise StateUnavailable: if the journal exists but cannot be replayed. Deliberately
        fatal: starting with an empty book when durable evidence of open positions
        exists but is unreadable is how a system re-enters a position it already holds.
    """
    config = config or AppConfig.from_env()
    config.data_dir.mkdir(parents=True, exist_ok=True)

    logger = StructuredLogger("kqr", events_path=config.ops_events_path)
    mode = FileTradingMode(config.mode_path).current()
    kill_switch = FileKillSwitch(config.kill_switch_path)

    source = ReplayMarketDataSource(config.sample_panel)
    store = ParquetBarStore(config.bar_store_dir)
    strategy = get_strategy(config.strategy_key)
    journal = JsonlOrderJournal(config.journal_path, broker_name="simulated")

    orders, fills = journal.replay()
    broker = SimulatedBroker.restore_from(
        orders,
        fills,
        initial_cash=config.initial_cash,
        kill_switch=kill_switch,
        journal=journal,
    )

    if mode is TradingMode.LIVE:
        # Stated rather than silently downgraded. A repository that quietly ran a
        # simulator while its mode file said LIVE would be the exact category of
        # misleading claim this project documents.
        logger.warning(
            "mode is LIVE but no live broker is bundled; orders will be simulated",
            mode=mode.value,
        )

    return System(
        config=config,
        logger=logger,
        mode=mode,
        kill_switch=kill_switch,
        source=source,
        store=store,
        strategy=strategy,
        journal=journal,
        broker=broker,
        restored_orders=len(orders),
        restored_fills=len(fills),
    )
