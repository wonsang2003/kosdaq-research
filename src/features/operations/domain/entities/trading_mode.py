"""Trading mode and the kill switch — the two controls an operator needs at 3am.

Both are deliberately **files on disk**, not environment variables and not code
constants. That choice is doing specific work:

  * A file survives a restart. An env var set in a shell does not, and a systemd
    unit change requires a redeploy.
  * A file can be changed without touching the process. `echo DRY > mode.txt` and
    `touch kill.flag` are things you can do over SSH with one hand while reading a
    dashboard with the other.
  * A file is inspectable. The deploy script can print the current mode as part of
    its verification, so "what is this box actually going to do" has an answer that
    does not involve reading code.

The default is the safest value, and an unreadable or unrecognised file resolves to
that default rather than raising. This is the one place in this codebase where
falling back is correct: refusing to start because the mode file is corrupt would
turn a typo into an outage, whereas defaulting to DRY turns it into a no-op.
"""

from __future__ import annotations

from enum import Enum


class TradingMode(str, Enum):
    """What the broker adapter is permitted to do."""

    DRY = "DRY"
    """No network calls, no orders. Every broker primitive short-circuits at its top
    and returns a sentinel. The default, and what the test suite runs under."""

    PAPER = "PAPER"
    """Real market data, simulated fills against the simulated broker. Orders are
    journalled exactly as live orders would be, so the journal format is exercised."""

    LIVE = "LIVE"
    """Real orders against a real account. This repository ships no credentials and
    no live adapter implementation, so selecting it here resolves to a broker that
    refuses every order with a stated reason rather than pretending."""

    @classmethod
    def parse(cls, raw: str | None) -> TradingMode:
        """Resolve a mode from file contents.

        :param raw: file contents, or None if absent.
        :return: the matching mode, or `DRY` for anything unrecognised — including
            empty, whitespace, and misspelt values. Never raises: a corrupt mode file
            must degrade to inaction, not to an outage.
        """
        if not raw:
            return cls.DRY
        candidate = raw.strip().upper()
        return cls.__members__.get(candidate, cls.DRY)

    @property
    def places_real_orders(self) -> bool:
        return self is TradingMode.LIVE

    @property
    def touches_network(self) -> bool:
        return self is not TradingMode.DRY
