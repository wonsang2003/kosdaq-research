"""Ports for getting bars in and storing them.

Two interfaces, split because they fail differently. A source is remote, rate-limited
and can be down; a store is local, must never lose data, and must be safe to write
from a process that may be killed at any moment.

The system this replaces had neither interface. Its recorder hard-instantiated a
concrete Kiwoom client in its constructor, so there was exactly one possible source
and no way to test the recorder without a network. A second broker adapter existed as
a file but raised `NotImplementedError` — which is the strongest available evidence
that an abstraction was never checked against two implementations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from src.features.market_data.domain.entities.bar import Bar, Quote
from src.shared.domain.errors import DomainError


class MarketDataUnavailable(DomainError):
    """Bars could not be retrieved.

    Raised rather than returning an empty list. An empty list is a *claim* that the
    market was quiet, and downstream a quiet market and a failed request are
    indistinguishable — which is how a rate-limited afternoon becomes a hole in a
    panel that looks exactly like a holiday.
    """


class RateLimited(MarketDataUnavailable):
    """The venue refused because the caller is over quota.

    Its own class because it is the one failure worth retrying unchanged. Note the
    venue this imitates signals it inside a **200 OK** body rather than with a 429,
    so a client that trusts the status code records a silent gap.
    """


class MarketDataSource(ABC):
    """Somewhere bars come from: a broker API, a replay of stored data, a simulator."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier recorded alongside stored bars, so a panel can always
        answer 'where did this row come from'."""
        raise NotImplementedError

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """Whether this source can serve requests right now.

        False for a live source with no credentials configured. The composition root
        checks this to choose a source, rather than discovering the problem as an
        exception in the middle of a session.
        """
        raise NotImplementedError

    @abstractmethod
    def fetch_bars(
        self,
        symbol: str,
        interval_minutes: int,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[Bar]:
        """Bars for one symbol, ascending by timestamp, deduplicated.

        :param since: inclusive lower bound; None means "as far back as available".
        :param until: inclusive upper bound; None means "up to now".
        :return: bars in ascending order. An **empty list means the venue genuinely
            reported no trading**, which is a real answer for a halted or untraded
            symbol.
        :raise MarketDataUnavailable: on any failure to retrieve.
        :raise RateLimited: when over quota, so the caller can back off rather than
            treating it as absence.
        """
        raise NotImplementedError

    @abstractmethod
    def fetch_quote(self, symbol: str) -> Quote | None:
        """Current top of book.

        :return: the quote, or None if this source cannot serve quotes at all — a
            historical replay, for instance. None means "not supported"; a locked or
            empty book is a `Quote` with zero ask, not None.
        :raise MarketDataUnavailable: on failure to retrieve from a source that does
            support quotes.
        """
        raise NotImplementedError


class BarStore(ABC):
    """Durable local storage for bars.

    The contract that matters is in `append_bars`: writes must be atomic and must
    never truncate. The implementation being replaced opened the target file with
    mode `"w"` and rewrote the whole symbol from memory on every save, so a SIGTERM
    during a routine append destroyed that symbol's entire history.
    """

    @abstractmethod
    def append_bars(self, bars: list[Bar]) -> int:
        """Persist bars, replacing any existing rows with the same (symbol, timestamp).

        Idempotent by contract: re-ingesting a page must be a no-op, because retry
        after a partial failure is normal and must not duplicate rows.

        :return: number of rows newly added, excluding those that were already present.
        :raise StateUnavailable: if the write fails. Must not partially apply — a
            caller that sees an exception must be able to retry the whole batch.
        """
        raise NotImplementedError

    @abstractmethod
    def load_bars(
        self,
        symbol: str,
        interval_minutes: int,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[Bar]:
        """Stored bars for one symbol, ascending.

        :return: bars in range; empty list when nothing is stored for that symbol.
            Absence here is unambiguous — the store either has rows or it does not.
        :raise StateUnavailable: if stored data exists but cannot be read.
        """
        raise NotImplementedError

    @abstractmethod
    def symbols(self, interval_minutes: int) -> list[str]:
        """Every symbol with stored data at this interval, sorted."""
        raise NotImplementedError

    @abstractmethod
    def last_timestamp(self, symbol: str, interval_minutes: int) -> datetime | None:
        """Newest stored bar time, for resuming an interrupted collection.

        :return: the timestamp, or None if nothing is stored. This is what makes
            collection resumable after a crash without re-fetching a whole history.
        """
        raise NotImplementedError
