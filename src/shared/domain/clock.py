"""The single time accessor.

Every timestamp in this system passes through this module. That is not tidiness —
it buys two things that are otherwise expensive:

**A fake clock costs one line.** `monkeypatch.setattr(clock, "now", lambda: ...)`
freezes the entire system. No library, no dependency injection ceremony, no
`freezegun`. The seam exists because there is exactly one accessor.

**Timezone bugs become impossible rather than unlikely.** The system this imitates
used naive `datetime.now()` in thirteen places with 09:00-15:30 as an implicit local
window. Deployed to a UTC container, every session gate would have fired nine hours
late — silently, since a gate that never opens logs nothing. Here the zone is
explicit at the only place a `datetime` is created.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

MARKET_TZ = ZoneInfo("Asia/Seoul")
"""Exchange timezone, stated rather than inherited from the host."""

SESSION_OPEN = time(9, 0)
SESSION_CLOSE = time(15, 30)
"""KRX regular session. The closing auction runs from 15:20 and prints at 15:30."""


def now() -> datetime:
    """Current time in exchange-local terms, always timezone-aware.

    :return: an aware datetime in `MARKET_TZ`. Never naive — a naive datetime that
        escapes into a comparison is the bug this module exists to prevent.
    """
    return datetime.now(MARKET_TZ)


def today() -> date:
    """Exchange-local calendar date.

    Deliberately derived from `now()` rather than `date.today()`, which reads the
    host timezone and would roll over at the wrong instant.
    """
    return now().date()


def hhmm(moment: datetime | None = None) -> str:
    """Zero-padded HHMM, the form the scheduler matches on.

    :param moment: defaults to `now()`. Must be timezone-aware if supplied.
    :raise ValueError: if a naive datetime is passed. Silently treating it as
        exchange-local is how an off-by-nine-hours bug gets into production.
    """
    moment = moment or now()
    if moment.tzinfo is None:
        raise ValueError("naive datetime passed to hhmm(); attach a timezone first")
    return moment.astimezone(MARKET_TZ).strftime("%H%M")


def day_key(moment: datetime | None = None) -> str:
    """`YYYY-MM-DD` in exchange-local terms — the scheduler's deduplication value."""
    moment = moment or now()
    if moment.tzinfo is None:
        raise ValueError("naive datetime passed to day_key(); attach a timezone first")
    return moment.astimezone(MARKET_TZ).strftime("%Y-%m-%d")


def is_within_session(moment: datetime | None = None) -> bool:
    """Whether the regular continuous session is open.

    Weekday-only. Public holidays are **not** handled here, deliberately: a holiday
    calendar needs a data source, and a stale calendar that silently claims a
    trading day is worse than no calendar. Callers that need holiday awareness ask
    the trading-calendar port, which is allowed to answer `None` for "unknown".
    """
    moment = (moment or now()).astimezone(MARKET_TZ)
    if moment.weekday() >= 5:
        return False
    return SESSION_OPEN <= moment.time() <= SESSION_CLOSE


def session_bounds(on: date | None = None) -> tuple[datetime, datetime]:
    """Aware open/close datetimes for a given calendar date.

    :return: (open, close) both in `MARKET_TZ`.
    """
    on = on or today()
    return (
        datetime.combine(on, SESSION_OPEN, tzinfo=MARKET_TZ),
        datetime.combine(on, SESSION_CLOSE, tzinfo=MARKET_TZ),
    )


def minutes_since_open(moment: datetime | None = None) -> int | None:
    """Minutes elapsed since the session opened.

    :return: elapsed minutes, or None when the market is not open. `None` rather
        than a negative number or zero, because "before the open" and "at the open"
        are different states and collapsing them hides bugs.
    """
    moment = (moment or now()).astimezone(MARKET_TZ)
    if not is_within_session(moment):
        return None
    opened, _ = session_bounds(moment.date())
    return int((moment - opened) / timedelta(minutes=1))
