"""Broker and journal ports.

The journal interface carries the single most consequential requirement in this
module, and it is a requirement the original did not meet:

**`replay` must be able to reconstruct working orders and positions.**

The system this imitates journalled every order and every fill to CSV and never read
either file back. Its positions lived in an in-memory dict. A restart therefore lost
every open order and every position, with full knowledge of both sitting on disk one
directory away. The journal was write-only, which means it was documentation rather
than state — and documentation does not survive an incident, which is the only time
it matters.

Here `replay` is on the interface, so an implementation that cannot recover state
cannot satisfy the contract.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.features.execution.domain.entities.order import Fill, Order, Position
from src.features.market_data.domain.entities.bar import Quote
from src.shared.domain.errors import DomainError


class OrderRejected(DomainError):
    """The broker refused the order. Carries the reason; never raised without one."""


class BrokerUnavailable(DomainError):
    """The broker could not be reached or is not configured.

    Distinct from `OrderRejected`: a rejection is an answer, this is the absence of
    one. A caller may retry this and must not retry a rejection.
    """


class Broker(ABC):
    """Somewhere orders go: a simulator, a paper account, a live account."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Identifier stamped onto every journalled order, so simulated and real rows
        in the same file remain separable forever."""
        raise NotImplementedError

    @property
    @abstractmethod
    def places_real_orders(self) -> bool:
        """Whether this implementation can move actual money.

        Read by the runtime banner. A simulator that answered True here, or a live
        adapter that answered False, would make the banner a lie — so this is checked
        by a test rather than trusted.
        """
        raise NotImplementedError

    @abstractmethod
    def submit(self, order: Order, quote: Quote | None = None) -> Order:
        """Submit an order and return it in its resulting state.

        :param quote: current book. When supplied, an implementation may fill
            immediately against it; when None the order rests until `on_quote`.
        :return: the order with `state` advanced. A refusal comes back as a
            `REJECTED` order carrying a reason — **not** as an exception — because a
            rejection is a normal outcome that must be journalled like any other.
        :raise OrderRejected: only for programming errors the caller could have
            avoided, such as a duplicate `idempotency_key`.
        :raise BrokerUnavailable: when the venue cannot be reached at all.
        :raise KillSwitchEngaged: when new risk is halted. Deliberately not caught
            here — submission is the path the switch exists to stop.
        """
        raise NotImplementedError

    @abstractmethod
    def cancel(self, order_id: str) -> Order:
        """Cancel a working order.

        :return: the order in its resulting state. Cancelling an already-terminal
            order returns it unchanged rather than raising — cancel is idempotent
            because the caller often cannot know whether a previous attempt landed.
        :raise BrokerUnavailable: when the venue cannot be reached.
        """
        raise NotImplementedError

    @abstractmethod
    def on_quote(self, quote: Quote) -> list[Fill]:
        """Advance resting orders against a new book.

        This is what drives simulated fills forward. Returning the fills rather than
        applying them silently keeps the caller able to journal each one.

        :return: fills produced by this quote, possibly empty.
        """
        raise NotImplementedError

    @abstractmethod
    def positions(self) -> dict[str, Position]:
        """Current net holdings by symbol.

        :raise BrokerUnavailable: if holdings cannot be determined. Must **not**
            return an empty dict on failure — an empty dict reads as "flat", and
            acting on a false flat means re-entering a position already held. Several
            paths in the imitated system bail rather than guess for this reason.
        """
        raise NotImplementedError

    @abstractmethod
    def working_orders(self) -> list[Order]:
        """Orders still able to receive fills."""
        raise NotImplementedError


class OrderJournal(ABC):
    """Durable, append-only record of orders and fills — and the source of truth
    after a restart."""

    @abstractmethod
    def record_order(self, order: Order) -> None:
        """Append an order state transition.

        :raise StateUnavailable: if the write fails. Not swallowed: state recovery
            trusts this file, so a silently dropped write becomes a lost position.
        """
        raise NotImplementedError

    @abstractmethod
    def record_fill(self, fill: Fill) -> None:
        """Append a fill.

        :raise StateUnavailable: if the write fails.
        """
        raise NotImplementedError

    @abstractmethod
    def replay(self) -> tuple[list[Order], list[Fill]]:
        """Rebuild everything the journal knows.

        The requirement the original failed. Called at startup so that working orders
        and positions survive a restart.

        :return: (orders in final journalled state, all fills) in write order. Later
            records for the same `order_id` supersede earlier ones.
        :raise StateUnavailable: if the journal exists but is unreadable. A missing
            journal is an ordinary first run and returns empty lists.
        """
        raise NotImplementedError
