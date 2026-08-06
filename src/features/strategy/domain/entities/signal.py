"""What a strategy emits.

A `Signal` is intentionally thin — an instrument, a direction, a time, a rank, and a
free-form reason. It carries no score, no probability, and no confidence number.

That omission is a design decision, not an oversight. A numeric quality score invites
ranking strategies against each other and then optimising the ranking, which is the
mechanism this repository exists to document: the measure becomes the target. Rank
within a single evaluation is enough to size and to cut, and it cannot be compared
across strategies, which is exactly the property wanted.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class Signal(BaseModel):
    """An intent to trade, emitted by a strategy at one instant."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    side: Side
    timestamp: datetime
    """The instant the signal was *decided*. Execution must transact strictly after
    this — a fill at a price stamped at or before this moment is a ghost leg, and one
    strategy in this repository's graveyard died to exactly that (t = 3.71 became
    t = 1.34 once the executable leg was used)."""

    rank: int = Field(ge=1)
    """Position within this evaluation, 1 = strongest. Relative only. Deliberately not
    comparable across evaluations or strategies."""

    reason: str = ""
    """Human-readable justification, written for whoever reads the journal after a bad
    day. Not parsed by anything."""

    @field_validator("timestamp")
    @classmethod
    def _must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Signal.timestamp must be timezone-aware")
        return value


class StrategySpec(BaseModel):
    """Identity and disclosure status of a strategy implementation.

    Every strategy must declare `verdict`, and the runtime prints it. This is how the
    repository keeps a promise it makes in prose: the shipped default is a strategy
    that was *refuted*, and a reviewer running it should be told so by the program
    rather than having to find the document.
    """

    model_config = ConfigDict(frozen=True)

    key: str
    name: str

    verdict: str
    """One of REFUTED / CONTROL / WITHHELD. There is no CONFIRMED implementation in
    this repository and there will not be one — see docs/DISCLOSURE.md."""

    summary: str
    """One line a reviewer reads in the runtime banner."""

    evidence: str = ""
    """Where the verdict comes from, as a repo-relative path."""

    @property
    def is_expected_to_lose(self) -> bool:
        """True when losing money is the documented, intended outcome.

        The runtime uses this to label its own results, so a reviewer cannot mistake a
        demonstration of failure for a failed demonstration.
        """
        return self.verdict in {"REFUTED", "CONTROL"}
