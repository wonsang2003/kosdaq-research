"""Generate the SYNTHETIC minute-bar panel this repository ships.

These are not market prices. Nothing here was observed anywhere. The panel exists so
that a reviewer with a clean clone and no broker account can run the collector, the
strategies, the execution model and the scheduler end to end, and so that the test
suite has a realistically shaped input instead of six hand-written rows.

**The process is deliberately edge-free.** Each session gets a drift drawn fresh from
a zero-mean normal, and that draw is not a function of anything observable in the
data — not the previous close, not the volume, not the time of day. So a strategy
fitted here can only find noise, and any "result" produced from this panel is a
demonstration of overfitting rather than of an edge. That is the point: it is the
correct input for testing plumbing and the wrong input for testing a hypothesis, and
the distinction is easier to keep if the data cannot possibly support the second use.

Determinism: every symbol draws from its own generator seeded with
`(SEED, symbol index)`. Re-running the script byte-for-byte reproduces the panel, and
adding a thirteenth symbol does not change the first twelve — which matters because a
regenerated sample that silently moved every price would invalidate every test
baseline in the repo at once.

Usage:
    python scripts/make_sample_data.py            # write data/samples/
    python scripts/make_sample_data.py --out /tmp/panel.parquet --sessions 3
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np

# `pytest.ini` sets `pythonpath = .`, so the test suite imports `src` without help.
# A reviewer running `python scripts/make_sample_data.py` has no such setting, and the
# first command in the README failing on ModuleNotFoundError costs more credibility
# than four lines of path handling.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.features.market_data.domain.entities.bar import Bar  # noqa: E402
from src.features.market_data.infrastructure.parquet_store import (  # noqa: E402
    bars_to_frame,
    write_bar_frame_atomically,
)
from src.shared.domain import clock  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "data" / "samples"
SAMPLE_FILE = SAMPLE_DIR / "SYNTHETIC_minute_bars.parquet"
README_FILE = SAMPLE_DIR / "README.md"

SEED = 20250901

SYMBOLS: tuple[str, ...] = tuple(f"SYN{index:03d}" for index in range(1, 13))
"""Fictional six-character codes. Deliberately not in KRX's numeric format: a code
like `035720` in a sample file invites someone to compare it against the real issue,
and the first person to do that will believe the numbers for about a minute."""

FIRST_SESSION = date(2025, 9, 1)
SESSION_COUNT = 20
"""Weekdays from `FIRST_SESSION`. Public holidays are not skipped — this panel has no
holiday calendar, for the same reason `clock.is_within_session` has none, and a
synthetic panel with an invented holiday would teach a reader something false about
the venue."""

SESSION_MINUTES = 391  # 09:00 … 15:30 inclusive

MINUTE_SIGMA = 0.0011
OVERNIGHT_SIGMA = 0.012
DAILY_DRIFT_SIGMA = 0.004
RANGE_SIGMA = 0.0009

UNTRADED_MINUTE_RATE = 0.015
"""Fraction of minutes with no print at all. Real minute panels are not dense in the
illiquid end of KOSDAQ, and a loader that assumes density is a loader that reports a
gap as a data error."""

HALT_RATE_PER_SESSION = 0.25
HALT_LENGTH = (5, 16)


def tick_size(price: float) -> int:
    """KRX-shaped price ladder.

    Prices are integers in KRW and move on a ladder, not continuously. A synthetic
    panel of floats would let a strategy take a fill at a price that cannot exist,
    and rounding at execution time instead of at generation time is how that goes
    unnoticed.
    """
    if price < 2_000:
        return 1
    if price < 5_000:
        return 5
    if price < 20_000:
        return 10
    if price < 50_000:
        return 50
    return 100


def _round_to_tick(price: float) -> float:
    step = tick_size(price)
    return float(max(step, round(price / step) * step))


def trading_days(first: date, count: int) -> list[date]:
    """`count` consecutive weekdays starting at or after `first`."""
    days: list[date] = []
    cursor = first
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def generate_bars(
    symbols: tuple[str, ...] = SYMBOLS,
    first_session: date = FIRST_SESSION,
    sessions: int = SESSION_COUNT,
    seed: int = SEED,
) -> list[Bar]:
    """The whole synthetic panel, ascending by (symbol, timestamp)."""
    days = trading_days(first_session, sessions)
    bars: list[Bar] = []
    for index, symbol in enumerate(symbols):
        bars.extend(_generate_symbol(symbol, days, np.random.default_rng([seed, index])))
    return bars


def _generate_symbol(
    symbol: str, days: list[date], rng: np.random.Generator
) -> list[Bar]:
    price = float(rng.uniform(1_500, 90_000))
    bars: list[Bar] = []

    for day in days:
        price = _round_to_tick(price * float(np.exp(rng.normal(0.0, OVERNIGHT_SIGMA))))
        # Drawn fresh each session and never exposed anywhere in the data. There is no
        # observable a rule could condition on to recover it.
        drift = float(rng.normal(0.0, DAILY_DRIFT_SIGMA)) / SESSION_MINUTES

        steps = rng.normal(drift, MINUTE_SIGMA, SESSION_MINUTES)
        closes = price * np.exp(np.cumsum(steps))
        opens = np.concatenate(([price], closes[:-1]))
        spreads = np.abs(rng.normal(0.0, RANGE_SIGMA, SESSION_MINUTES))
        volumes = rng.lognormal(6.4, 1.15, SESSION_MINUTES)

        skipped = _untraded_minutes(rng)
        session_open, _ = clock.session_bounds(day)

        for minute in range(SESSION_MINUTES):
            if minute in skipped:
                continue
            bar_open = _round_to_tick(float(opens[minute]))
            bar_close = _round_to_tick(float(closes[minute]))
            reach = 1.0 + float(spreads[minute])
            high = _round_to_tick(max(bar_open, bar_close) * reach)
            low = _round_to_tick(min(bar_open, bar_close) / reach)
            # Rounding can invert an already narrow bar; repair it here rather than
            # letting `Bar`'s validator reject a row at load time months later.
            high = max(high, bar_open, bar_close)
            low = min(low, bar_open, bar_close)

            volume = float(int(volumes[minute]))
            typical = (high + low + bar_close) / 3
            bars.append(
                Bar(
                    symbol=symbol,
                    timestamp=session_open + timedelta(minutes=minute),
                    interval_minutes=1,
                    open=bar_open,
                    high=high,
                    low=low,
                    close=bar_close,
                    volume=volume,
                    traded_value=float(round(volume * typical)),
                )
            )
        price = _round_to_tick(float(closes[-1]))
    return bars


def _untraded_minutes(rng: np.random.Generator) -> set[int]:
    """Minutes with no print: scattered thin ones, plus an occasional halt-shaped run."""
    skipped = set(
        int(minute)
        for minute in np.flatnonzero(rng.random(SESSION_MINUTES) < UNTRADED_MINUTE_RATE)
    )
    if rng.random() < HALT_RATE_PER_SESSION:
        start = int(rng.integers(30, SESSION_MINUTES - HALT_LENGTH[1]))
        length = int(rng.integers(*HALT_LENGTH))
        skipped.update(range(start, start + length))
    skipped.discard(0)  # keep the opening print; the open is what most rules key on
    return skipped


README_TEXT = """\
# SYNTHETIC sample data — not market data

Everything in this directory is **generated**. It was produced by
`scripts/make_sample_data.py` from a seeded random number generator. No price,
volume or timestamp here was observed on any exchange, and the symbols
(`SYN001`…`SYN012`) are fictional — they are not KRX issue codes and do not
correspond to any listed company.

## Why it exists

So that `make verify` works on a clean clone with no network, no credentials and no
broker account. The replay data source
(`src/features/market_data/infrastructure/replay_source.py`) reads
`SYNTHETIC_minute_bars.parquet`, which lets the collector, the strategies, the
execution model and the scheduler all be exercised end to end offline.

## What it must not be used for

**Do not backtest a hypothesis on this panel.** The generator draws each session's
drift from a zero-mean normal and exposes it nowhere in the data — there is no
observable a rule could condition on to recover it. Any strategy that appears
profitable here has fitted noise, by construction. A result from this file is
evidence about the code, never about a market.

## Shape

| | |
|---|---|
| symbols | 12 fictional (`SYN001`…`SYN012`) |
| sessions | 20 consecutive weekdays from 2025-09-01 |
| interval | 1 minute, 09:00–15:30 KST (391 slots per session) |
| gaps | ~1.5% of minutes untraded, plus occasional halt-shaped runs |
| prices | integer KRW on a KRX-shaped tick ladder |
| timestamps | timezone-aware, `Asia/Seoul` |

Bar timestamps are **bar-open** times, matching `Bar`'s documented convention.

## Regenerating

    python scripts/make_sample_data.py

Deterministic: each symbol draws from a generator seeded with `(SEED, symbol index)`,
so a re-run reproduces the file byte for byte and adding a symbol leaves the existing
ones untouched.
"""


def write_sample(
    out: Path = SAMPLE_FILE,
    readme: Path | None = README_FILE,
    symbols: tuple[str, ...] = SYMBOLS,
    first_session: date = FIRST_SESSION,
    sessions: int = SESSION_COUNT,
    seed: int = SEED,
) -> tuple[Path, int]:
    """Generate and publish the panel atomically.

    :return: (path written, row count).
    """
    bars = generate_bars(symbols, first_session, sessions, seed)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_bar_frame_atomically(bars_to_frame(bars), out)
    if readme is not None:
        readme.parent.mkdir(parents=True, exist_ok=True)
        readme.write_text(README_TEXT, encoding="utf-8")
    return out, len(bars)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=SAMPLE_FILE)
    parser.add_argument("--sessions", type=int, default=SESSION_COUNT)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument(
        "--no-readme",
        action="store_true",
        help="skip the README (it belongs next to the default output, not a scratch copy)",
    )
    args = parser.parse_args(argv)

    path, rows = write_sample(
        out=args.out,
        readme=None if args.no_readme else README_FILE,
        sessions=args.sessions,
        seed=args.seed,
    )
    span = f"{len(SYMBOLS)} synthetic symbols x {args.sessions} sessions"
    print(f"wrote {rows:,} SYNTHETIC minute bars ({span}) to {path}")
    print("these are not market prices; see data/samples/README.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
