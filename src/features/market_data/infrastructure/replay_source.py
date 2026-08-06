"""A `MarketDataSource` that replays a bar file shipped with the repository.

This exists so the whole system can be exercised — collector, strategy, execution,
scheduler — with no network, no credentials and no broker account. Every result a
reviewer can reproduce from a clean clone comes through here.

Two decisions in this file are about honesty rather than convenience.

**`fetch_quote` returns None, always.** A historical panel has no book. The contract
distinguishes `None` ("this source cannot serve quotes") from a `Quote` with a zero
ask ("the book is locked; there is no seller"). Collapsing those two would let a
backtest read a replayed row as a live, fillable price. That is not hypothetical: the
graveyard in this repository contains a strategy that survived to `t = 3.71` on
exactly that mistake and died at `t = 1.34` once the executable leg was used.

**A missing or unreadable sample file raises.** It does not degrade into an empty
list. An empty list is the answer for a symbol that genuinely did not trade, and a
deployment that shipped without its data must not be able to impersonate a quiet
market — a whole-panel absence looks, downstream, exactly like a market holiday.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from src.features.market_data.domain.entities.bar import Bar, Quote
from src.features.market_data.domain.repositories.market_data import (
    MarketDataSource,
    MarketDataUnavailable,
)
from src.features.market_data.infrastructure.parquet_store import (
    frame_to_bars,
    read_bar_frame,
)

REPO_ROOT = Path(__file__).resolve().parents[4]

DEFAULT_SAMPLE_PATH = REPO_ROOT / "data" / "samples" / "SYNTHETIC_minute_bars.parquet"
"""The panel shipped with the repository. `SYNTHETIC` is in the filename, not only in
the documentation, because a file copied out of `data/samples/` loses its README and
the next person to find it has to be able to tell from the name alone that these are
not market prices."""


class ReplayMarketDataSource(MarketDataSource):
    """Serves stored bars as if they were arriving from a venue.

    Reads the same on-disk schema `ParquetBarStore` writes, so a panel collected from
    a live source can be replayed without a conversion step.
    """

    def __init__(self, sample_path: Path | str | None = None) -> None:
        self._path = Path(sample_path) if sample_path is not None else DEFAULT_SAMPLE_PATH
        self._cache: pd.DataFrame | None = None
        self._cache_key: tuple[int, int] | None = None

    @property
    def name(self) -> str:
        return "replay"

    @property
    def path(self) -> Path:
        return self._path

    @property
    def is_available(self) -> bool:
        """Whether the sample file is present.

        Checked against the filesystem on every call rather than at construction: the
        composition root builds sources before the generator script may have run, and
        a source that memoised `False` at import time would stay unavailable for the
        life of the process.
        """
        return self._path.is_file()

    def fetch_bars(
        self,
        symbol: str,
        interval_minutes: int,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[Bar]:
        """Replay one symbol's bars, ascending, deduplicated, bounds inclusive.

        :return: an empty list when the panel holds this symbol and interval but
            nothing inside the requested window — a real answer for an overnight gap
            or a symbol that was halted all day.
        :raise MarketDataUnavailable: if the panel is missing or unreadable, or if it
            holds no bars at `interval_minutes` at all. The second case is a request
            this replay cannot serve; answering `[]` would claim the market was quiet
            at a resolution that was never collected.
        """
        if interval_minutes <= 0:
            raise ValueError(f"interval_minutes must be positive, got {interval_minutes}")
        for label, bound in (("since", since), ("until", until)):
            if bound is not None and bound.tzinfo is None:
                raise ValueError(f"{label} must be timezone-aware")

        frame = self._panel()
        available = set(frame["interval_minutes"].unique())
        if interval_minutes not in available:
            raise MarketDataUnavailable(
                f"{self._path.name} holds no {interval_minutes}-minute bars "
                f"(has: {sorted(int(value) for value in available)})"
            )

        rows = frame[
            (frame["symbol"] == symbol)
            & (frame["interval_minutes"] == interval_minutes)
        ]
        if since is not None:
            rows = rows[rows["timestamp"] >= pd.Timestamp(since)]
        if until is not None:
            rows = rows[rows["timestamp"] <= pd.Timestamp(until)]

        rows = rows.drop_duplicates(subset="timestamp", keep="last")
        rows = rows.sort_values("timestamp", kind="stable", ignore_index=True)
        try:
            return frame_to_bars(rows)
        except (ValueError, KeyError) as exc:
            raise MarketDataUnavailable(f"corrupt rows in {self._path}") from exc

    def fetch_quote(self, symbol: str) -> Quote | None:
        """Always None: a replay has no book.

        See the module docstring. `None` means "not supported" and is deliberately not
        a `Quote` with a zero ask, which would mean "locked, no seller".
        """
        return None

    def available_symbols(self) -> list[str]:
        """Every symbol in the panel, sorted.

        Not part of the port — a live venue could not answer it cheaply — but a replay
        can, and tests and the sample-data script both need it.

        :raise MarketDataUnavailable: if the panel is missing or unreadable.
        """
        return sorted(str(value) for value in self._panel()["symbol"].unique())

    def _panel(self) -> pd.DataFrame:
        """The whole panel, cached against the file's mtime and size.

        Keyed on the file's identity rather than cached once, so a process that
        regenerates the sample mid-run serves the new bars instead of quietly
        replaying the ones it read at startup.

        :raise MarketDataUnavailable: on absence or any read failure.
        """
        try:
            stat = self._path.stat()
        except OSError as exc:
            raise MarketDataUnavailable(
                f"replay panel not found: {self._path} "
                "(run scripts/make_sample_data.py to generate it)"
            ) from exc

        key = (stat.st_mtime_ns, stat.st_size)
        if self._cache is not None and self._cache_key == key:
            return self._cache

        try:
            frame = read_bar_frame(self._path)
        except FileNotFoundError as exc:
            raise MarketDataUnavailable(f"replay panel not found: {self._path}") from exc
        except (OSError, ValueError, KeyError) as exc:
            raise MarketDataUnavailable(f"unreadable replay panel: {self._path}") from exc

        self._cache, self._cache_key = frame, key
        return frame
