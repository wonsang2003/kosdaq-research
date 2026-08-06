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
