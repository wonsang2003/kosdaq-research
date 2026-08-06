"""JSONL order journal — the file a restart reads.

The system this imitates wrote orders and fills to CSV and never read either back.
Its positions lived in an in-memory dict, so a restart lost every open order and
every position while a complete account of both sat on disk one directory away. The
journal was write-only, which makes it documentation rather than state, and
documentation does not survive an incident — the only moment it matters.

So the interesting method here is `replay`, not `record_*`. Two decisions in it are
worth stating:

**Later records supersede earlier ones by `order_id`.** The journal is a transition
log, not a snapshot: one order appears as OPEN, then PARTIALLY_FILLED, then FILLED.
Replay collapses that to the last state written.

**A missing file and an unreadable file are different.** Missing is an ordinary first
run and returns empty lists. Unreadable raises, because an empty result there would
claim "no open orders", and a system that starts flat when it is not flat re-enters
positions it already holds.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from src.features.execution.domain.entities.order import Fill, Order
from src.features.execution.domain.repositories.broker import OrderJournal
from src.features.operations.infrastructure.atomic_store import (
    JsonlAppender,
    StateUnavailable,
)
from src.shared.domain import clock

_ORDER = "order"
_FILL = "fill"


class JsonlOrderJournal(OrderJournal):
    """Append-only order journal backed by one JSONL file.

    Built on `JsonlAppender` rather than reimplementing the append: that class already
    does `flush` + `fsync` per record and already treats a torn final line as an
    interrupted write rather than corruption. Duplicating persistence logic is how the
    original ended up with a locked, atomic write in one module and an unprotected
    truncate-then-write in another for state that mattered just as much.
    """

    def __init__(self, path: Path | str, *, broker_name: str = "") -> None:
        """
        :param broker_name: stamped onto every record. Simulated and live rows can
            legitimately share a file across a promotion, and a row that does not say
            which broker produced it is unclassifiable forever afterwards.
        """
        self._appender = JsonlAppender(path)
        self._broker_name = broker_name

    @property
    def path(self) -> Path:
        return self._appender.path

    @property
    def broker_name(self) -> str:
        return self._broker_name

    # ── writes ───────────────────────────────────────────────────────────────
    def record_order(self, order: Order) -> None:
        """Append one order state transition.

        :raise StateUnavailable: if the write fails; not swallowed, because state
            recovery trusts this file and a dropped write becomes a lost position.
        """
        self._appender.append(self._envelope(_ORDER, order.model_dump(mode="json")))

    def record_fill(self, fill: Fill) -> None:
        """Append one fill.

        Not idempotent, and cannot be: `Fill` carries no identity of its own, so two
        identical records are indistinguishable from one execution reported twice.
        Call this exactly once per fill.

        :raise StateUnavailable: if the write fails.
        """
        self._appender.append(self._envelope(_FILL, fill.model_dump(mode="json")))

    def _envelope(self, kind: str, payload: dict) -> dict:
        return {
            "kind": kind,
            "broker": self._broker_name,
            "recorded_at": clock.now().isoformat(),
            "payload": payload,
        }

    # ── recovery ─────────────────────────────────────────────────────────────
    def replay(self) -> tuple[list[Order], list[Fill]]:
        """Rebuild everything the journal knows.

        :return: (orders in their final journalled state, every fill in write order).
            Orders come back in first-appearance order, which is submission order.
        :raise StateUnavailable: if the file exists but cannot be read, holds a record
            of an unrecognised kind, or holds a payload that no longer validates. All
            three are refused rather than skipped: skipping a record silently drops an
            order, and an order dropped from recovery is an untracked live position.
        """
        orders: dict[str, Order] = {}
        fills: list[Fill] = []

        for number, record in enumerate(self._appender.read_all(), start=1):
            if not isinstance(record, dict):
                raise StateUnavailable(f"journal record {number} is not an object: {self.path}")

            kind = record.get("kind")
            payload = record.get("payload")
            if not isinstance(payload, dict):
                raise StateUnavailable(f"journal record {number} has no payload: {self.path}")

            if kind == _ORDER:
                order = self._parse(Order, payload, number)
                # Insertion order is preserved on update, so re-recording an order in
                # a later state keeps it in its original submission position.
                orders[order.order_id] = order
            elif kind == _FILL:
                fills.append(self._parse(Fill, payload, number))
            else:
                raise StateUnavailable(
                    f"journal record {number} has unknown kind {kind!r}: {self.path}"
                )

        return list(orders.values()), fills

    def _parse(self, model, payload: dict, number: int):
        try:
            return model.model_validate(payload)
        except ValidationError as exc:
            raise StateUnavailable(
                f"journal record {number} does not validate as {model.__name__}: {self.path}"
            ) from exc
