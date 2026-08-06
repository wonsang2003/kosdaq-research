"""Market data infrastructure: replay source, parquet store, quality report.

Each test names the failure it guards. Two of them guard failures that actually
happened in the system this repository imitates — a `mode="w"` append that destroyed a
symbol's history, and a duplicated row set that fabricated an edge — and those two are
the reason the other twenty-six exist.

The suite runs under `TZ=UTC` and says so where it matters: a timezone test that only
passes on a machine set to Seoul time is worse than no timezone test.
"""

from __future__ import annotations

import inspect
import os
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from scripts.make_sample_data import (
    SAMPLE_FILE,
    SYMBOLS,
    generate_bars,
    write_sample,
)
from src.features.market_data.domain.entities.bar import Bar, Quote
from src.features.market_data.domain.repositories.market_data import (
    MarketDataUnavailable,
)
from src.features.market_data.infrastructure import parquet_store
from src.features.market_data.infrastructure.parquet_store import (
    BarFrameError,
    ParquetBarStore,
    bars_to_frame,
)
from src.features.market_data.infrastructure.quality import (
    analyze_bars,
    expected_bars_per_session,
)
from src.features.market_data.infrastructure.replay_source import (
    ReplayMarketDataSource,
)
from src.features.operations.infrastructure.atomic_store import StateUnavailable
from src.shared.domain import clock

FIRST_OPEN = datetime(2025, 9, 1, 9, 0, tzinfo=clock.MARKET_TZ)


def make_bar(
    minute: int,
    symbol: str = "000660",
    price: float = 10_000.0,
    interval: int = 1,
    at: datetime | None = None,
) -> Bar:
    stamp = at if at is not None else FIRST_OPEN + timedelta(minutes=minute)
    return Bar(
        symbol=symbol,
        timestamp=stamp,
        interval_minutes=interval,
        open=price,
        high=price + 10,
        low=price - 10,
        close=price,
        volume=100.0,
        traded_value=100.0 * price,
    )


@pytest.fixture(scope="module")
def shipped() -> ReplayMarketDataSource:
    """The panel the repository actually ships, not a fixture built in memory.

    Deliberately the real file: a replay source that passes against a synthetic
    six-row frame and fails against the shipped panel is the only interesting failure
    here, and an in-memory fixture cannot find it.
    """
    return ReplayMarketDataSource()


# ── replay source ────────────────────────────────────────────────────────────
def test_the_replay_source_identifies_itself_and_reports_availability(shipped):
    """`name` is recorded next to stored bars so a panel can always answer 'where did
    this row come from'."""
    assert shipped.name == "replay"
    assert shipped.is_available is True


def test_a_missing_sample_file_raises_rather_than_returning_an_empty_list(tmp_path):
    """The single most important refusal in this module.

    An empty list is a *claim* that the market was quiet. A deployment that shipped
    without its data must not be able to make that claim — downstream, a whole-panel
    absence is indistinguishable from a market holiday, and the resulting hole is
    never investigated because it always looks plausible.
    """
    source = ReplayMarketDataSource(tmp_path / "absent.parquet")
    assert source.is_available is False
    with pytest.raises(MarketDataUnavailable, match="not found"):
        source.fetch_bars("000660", 1)


def test_an_unreadable_sample_file_raises_rather_than_returning_an_empty_list(tmp_path):
    """Corrupt and absent are both failures; neither is a quiet market."""
    broken = tmp_path / "SYNTHETIC_minute_bars.parquet"
    broken.write_bytes(b"this is not a parquet file")
    source = ReplayMarketDataSource(broken)
    assert source.is_available is True  # the file exists, it is simply not readable
    with pytest.raises(MarketDataUnavailable, match="unreadable"):
        source.fetch_bars("000660", 1)


def test_fetch_quote_is_none_because_a_replay_has_no_book(shipped):
    """None means 'this source cannot serve quotes'. It is deliberately *not* a Quote
    with a zero ask, which means 'the book is locked, there is no seller'.

    Collapsing the two would let a backtest read a replayed row as a live, fillable
    price. A strategy in this repository's graveyard survived to t = 3.71 on exactly
    that error and died at t = 1.34 once the executable leg was used.
    """
    assert shipped.fetch_quote("SYN001") is None

    locked = Quote(
        symbol="SYN001", timestamp=FIRST_OPEN, bid=1_000.0, ask=0.0, bid_size=500.0
    )
    assert locked.is_locked
    assert locked is not None  # a locked book is a value, not an absence


def test_bars_come_back_ascending_and_deduplicated(shipped):
    stamps = [bar.timestamp for bar in shipped.fetch_bars("SYN001", 1)]
    assert stamps == sorted(stamps)
    assert len(stamps) == len(set(stamps))


def test_since_and_until_are_inclusive_bounds(shipped):
    """Inclusive at both ends, as the port documents. An exclusive `until` silently
    drops the closing bar, which is the one most rules key on."""
    until = FIRST_OPEN + timedelta(minutes=30)
    bars = shipped.fetch_bars("SYN001", 1, since=FIRST_OPEN, until=until)
    assert bars[0].timestamp == FIRST_OPEN
    assert bars[-1].timestamp == until
    assert all(FIRST_OPEN <= bar.timestamp <= until for bar in bars)


def test_a_window_with_no_trading_is_a_legitimate_empty_list(shipped):
    """The other side of the coin: absence that is *real* must not raise.

    Nothing trades overnight. The port says an empty list means the venue genuinely
    reported no trading, and this is that case — the file is present and readable, the
    symbol is in it, and the answer for 19:00–23:00 is nothing.
    """
    evening = datetime(2025, 9, 1, 19, 0, tzinfo=clock.MARKET_TZ)
    assert shipped.fetch_bars("SYN001", 1, since=evening, until=evening + timedelta(hours=4)) == []


def test_a_symbol_absent_from_the_panel_is_an_empty_list_not_an_error(shipped):
    """A replay's universe is its file. A symbol that was never collected has no bars,
    which is absence, not failure."""
    assert shipped.fetch_bars("ZZZ999", 1) == []


def test_an_interval_the_panel_never_collected_raises(shipped):
    """Not an empty list. Answering `[]` would claim the market was quiet at a
    resolution nobody ever recorded — a claim about the venue standing in for a fact
    about the collection."""
    with pytest.raises(MarketDataUnavailable, match="no 5-minute bars"):
        shipped.fetch_bars("SYN001", 5)


def test_replayed_timestamps_are_aware_and_land_inside_the_kst_session(shipped):
    """Stated in UTC terms on purpose. If the zone were ever dropped from the parquet
    round-trip, this fails under `TZ=UTC` instead of quietly passing on a laptop that
    happens to be set to Seoul time."""
    bars = shipped.fetch_bars("SYN001", 1, since=FIRST_OPEN, until=FIRST_OPEN)
    assert len(bars) == 1
    stamp = bars[0].timestamp
    assert stamp.tzinfo is not None
    # 09:00 KST is 00:00 UTC. Written out rather than compared to `FIRST_OPEN`, which
    # would pass even if both sides had lost the zone together.
    assert stamp.astimezone(timezone.utc) == datetime(2025, 9, 1, 0, 0, tzinfo=timezone.utc)
    assert clock.is_within_session(stamp)
    assert clock.hhmm(stamp) == "0900"


def test_naive_bounds_are_refused_rather_than_assumed_to_be_local(shipped):
    """Comparing a naive bound against an aware column raises inside pandas with a
    message about nothing in particular. Refusing here names the actual mistake."""
    with pytest.raises(ValueError, match="since must be timezone-aware"):
        shipped.fetch_bars("SYN001", 1, since=datetime(2025, 9, 1, 9, 0))


def test_a_panel_with_naive_timestamps_is_refused(tmp_path):
    """Refused, not assumed to be exchange-local. Assuming is how a panel collected in
    a UTC container is read back nine hours off: every session filter still passes and
    every bar is simply attributed to the wrong minute."""
    frame = bars_to_frame([make_bar(index) for index in range(3)])
    frame["timestamp"] = frame["timestamp"].dt.tz_localize(None)
    path = tmp_path / "naive.csv"
    frame.to_csv(path, index=False)
    with pytest.raises(MarketDataUnavailable, match="unreadable"):
        ReplayMarketDataSource(path).fetch_bars("000660", 1)


def test_the_panel_cache_follows_the_file_not_the_process(tmp_path):
    """A process that regenerates the sample mid-run must serve the new bars. Caching
    once at first read means a collector's own output is invisible to it until
    restart."""
    path = tmp_path / "panel.parquet"
    parquet_store.write_bar_frame_atomically(bars_to_frame([make_bar(0)]), path)
    source = ReplayMarketDataSource(path)
    assert len(source.fetch_bars("000660", 1)) == 1

    parquet_store.write_bar_frame_atomically(
        bars_to_frame([make_bar(index) for index in range(4)]), path
    )
    os.utime(path, (0, 0))  # force a distinct mtime, not merely a different one
    assert len(source.fetch_bars("000660", 1)) == 4


# ── the store: atomicity and idempotence ─────────────────────────────────────
def test_append_then_load_round_trips(tmp_path):
    store = ParquetBarStore(tmp_path)
    written = [make_bar(index) for index in range(5)]
    assert store.append_bars(written) == 5
    loaded = store.load_bars("000660", 1)
    assert [bar.timestamp for bar in loaded] == [bar.timestamp for bar in written]
    assert loaded[0].close == written[0].close


def test_re_ingesting_the_same_rows_adds_nothing(tmp_path):
    """Retry after a partial failure is normal. A store that duplicated on retry would
    turn every network hiccup into a fabricated statistic — see `QualityReport`."""
    store = ParquetBarStore(tmp_path)
    page = [make_bar(index) for index in range(50)]
    assert store.append_bars(page) == 50
    assert store.append_bars(page) == 0
    assert store.append_bars(page) == 0
    assert len(store.load_bars("000660", 1)) == 50


def test_repeated_appends_never_truncate_the_history(tmp_path):
    """The incident this whole module answers to.

    The implementation being replaced opened the target with mode `"w"` and rewrote
    the symbol from memory on every save. Here each append carries only the new rows
    and the stored history must survive all of them.
    """
    store = ParquetBarStore(tmp_path)
    store.append_bars([make_bar(index) for index in range(200)])
    for index in range(200, 260):
        assert store.append_bars([make_bar(index)]) == 1
    assert len(store.load_bars("000660", 1)) == 260


def test_a_crash_during_the_rename_leaves_the_previous_history_intact(tmp_path, monkeypatch):
    """A SIGTERM between truncate and write is what destroyed the original's history.

    Simulated by making the publish step fail. Because the target is only ever
    replaced — never opened for writing — the old file is still whole afterwards, and
    the interrupted batch can simply be retried.
    """
    store = ParquetBarStore(tmp_path)
    store.append_bars([make_bar(index) for index in range(100)])

    def explode(*_args, **_kwargs):
        raise OSError("killed mid-publish")

    monkeypatch.setattr(parquet_store.os, "replace", explode)
    with pytest.raises(StateUnavailable):
        store.append_bars([make_bar(index) for index in range(100, 150)])

    monkeypatch.undo()
    assert len(store.load_bars("000660", 1)) == 100
    assert store.append_bars([make_bar(index) for index in range(100, 150)]) == 50
    assert len(store.load_bars("000660", 1)) == 150


def test_a_failed_append_leaves_no_temp_file_behind(tmp_path, monkeypatch):
    """A stray `.tmp` beside real history is found by the next glob and read as data."""
    store = ParquetBarStore(tmp_path)
    store.append_bars([make_bar(0)])
    monkeypatch.setattr(
        parquet_store.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("x"))
    )
    with pytest.raises(StateUnavailable):
        store.append_bars([make_bar(1)])
    assert list(tmp_path.rglob("*.tmp")) == []


def test_the_store_publishes_with_os_replace_and_never_opens_the_target_for_writing(tmp_path):
    """Structural, because the behavioural test above cannot distinguish 'atomic' from
    'fast enough that the race did not fire'. The absence of a truncating open is the
    property, and it is checkable directly."""
    source = inspect.getsource(parquet_store)
    assert "os.replace" in source
    assert 'open(tmp, "rb")' in source
    assert 'mode="w"' not in source


def test_a_row_with_a_stored_timestamp_replaces_it_and_is_not_counted_as_new(tmp_path):
    """The port says same-timestamp rows are replaced. A restatement — a venue
    correcting a bar after the fact — must not appear as an extra observation."""
    store = ParquetBarStore(tmp_path)
    store.append_bars([make_bar(index, price=10_000.0) for index in range(10)])
    assert store.append_bars([make_bar(5, price=12_345.0)]) == 0
    loaded = store.load_bars("000660", 1)
    assert len(loaded) == 10
    assert loaded[5].close == 12_345.0


def test_the_same_instant_in_a_different_zone_is_the_same_bar(tmp_path):
    """A collector that switches from a UTC-stamped endpoint to a KST-stamped one
    would otherwise double every row in the overlap, and the duplicate would be
    invisible to inspection because both timestamps are correct."""
    store = ParquetBarStore(tmp_path)
    store.append_bars([make_bar(0)])
    same_instant = make_bar(0, at=datetime(2025, 9, 1, 0, 0, tzinfo=timezone.utc))
    assert store.append_bars([same_instant]) == 0
    assert len(store.load_bars("000660", 1)) == 1


def test_an_empty_batch_is_a_no_op(tmp_path):
    assert ParquetBarStore(tmp_path).append_bars([]) == 0


# ── the store: layout, resumability, refusals ────────────────────────────────
def test_last_timestamp_makes_collection_resumable(tmp_path):
    """None before anything is stored — not epoch, not now(). A fabricated resume
    point either re-fetches a whole history or skips one."""
    store = ParquetBarStore(tmp_path)
    assert store.last_timestamp("000660", 1) is None

    store.append_bars([make_bar(index) for index in range(30)])
    resume = store.last_timestamp("000660", 1)
    assert resume == FIRST_OPEN + timedelta(minutes=29)
    assert resume.tzinfo is not None

    following = [make_bar(index) for index in range(30, 40)]
    assert store.append_bars([bar for bar in following if bar.timestamp > resume]) == 10
    assert store.last_timestamp("000660", 1) == FIRST_OPEN + timedelta(minutes=39)


def test_intervals_are_stored_separately(tmp_path):
    """A one-minute panel and a five-minute panel of the same issue must not share a
    file, so re-deriving one cannot corrupt the other."""
    store = ParquetBarStore(tmp_path)
    store.append_bars([make_bar(0, interval=1)])
    store.append_bars([make_bar(0, interval=5, price=999.0)])
    assert store.symbols(1) == ["000660"]
    assert store.symbols(5) == ["000660"]
    assert len(store.load_bars("000660", 1)) == 1
    assert store.load_bars("000660", 5)[0].close == 999.0
    assert store.last_timestamp("000660", 15) is None


def test_symbols_is_sorted_and_empty_before_anything_is_stored(tmp_path):
    store = ParquetBarStore(tmp_path)
    assert store.symbols(1) == []
    store.append_bars([make_bar(0, symbol=code) for code in ("000990", "000660", "000770")])
    assert store.symbols(1) == ["000660", "000770", "000990"]


def test_load_bars_honours_an_inclusive_window(tmp_path):
    store = ParquetBarStore(tmp_path)
    store.append_bars([make_bar(index) for index in range(20)])
    window = store.load_bars(
        "000660",
        1,
        since=FIRST_OPEN + timedelta(minutes=5),
        until=FIRST_OPEN + timedelta(minutes=9),
    )
    assert [bar.timestamp for bar in window] == [
        FIRST_OPEN + timedelta(minutes=offset) for offset in range(5, 10)
    ]


def test_load_bars_is_empty_for_an_unknown_symbol_but_raises_for_a_corrupt_file(tmp_path):
    """Nothing stored is an unambiguous empty. Stored-but-unreadable is not: a caller
    that reads corruption as absence re-collects a history it already has, or trusts a
    hole."""
    store = ParquetBarStore(tmp_path)
    assert store.load_bars("000660", 1) == []

    target = store.path_for("000660", 1)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"not parquet")
    with pytest.raises(StateUnavailable, match="unreadable"):
        store.load_bars("000660", 1)
    with pytest.raises(StateUnavailable, match="unreadable"):
        store.last_timestamp("000660", 1)


def test_a_symbol_that_would_escape_the_store_directory_is_refused(tmp_path):
    """Symbols become path components. A silently sanitised symbol writes one issue's
    bars into another issue's file, and nothing downstream can detect the swap."""
    store = ParquetBarStore(tmp_path)
    with pytest.raises(ValueError, match="path components"):
        store.path_for("../../etc/passwd", 1)
    with pytest.raises(ValueError, match="path components"):
        store.load_bars("000660/000661", 1)


def test_an_unwritable_root_raises_instead_of_silently_dropping_the_batch(tmp_path):
    blocked = tmp_path / "ro"
    blocked.mkdir()
    os.chmod(blocked, 0o500)
    try:
        with pytest.raises(StateUnavailable):
            ParquetBarStore(blocked).append_bars([make_bar(0)])
    finally:
        os.chmod(blocked, 0o700)


def test_a_stored_file_is_self_describing(tmp_path):
    """Symbol and interval live in the columns as well as the path. A parquet file
    copied out of the tree keeps its meaning; a path-only convention loses it."""
    store = ParquetBarStore(tmp_path)
    store.append_bars([make_bar(0, symbol="000770", interval=5)])
    frame = pd.read_parquet(store.path_for("000770", 5))
    assert set(frame["symbol"]) == {"000770"}
    assert set(frame["interval_minutes"]) == {5}


# ── quality ──────────────────────────────────────────────────────────────────
def test_the_quality_report_catches_a_seeded_duplicate_row(tmp_path):
    """The defect that once fabricated a +1.5%/day edge here.

    Nothing in a backtest can find it: the arithmetic is valid and the Sharpe is real.
    Counting at the boundary is the only place it is visible.
    """
    bars = [make_bar(index) for index in range(10)]
    seeded = bars + [bars[3], bars[7]]
    report = analyze_bars("000660", seeded)
    assert report.rows == 12
    assert report.duplicate_timestamps == 2
    assert not report.is_clean
    assert any("duplicate" in warning for warning in report.warnings)


def test_a_clean_panel_reports_clean(tmp_path):
    """An empty `warnings` tuple is the positive statement that the checks ran."""
    bars = [make_bar(index) for index in range(60)]
    report = analyze_bars("000660", bars, expected_per_day=60)
    assert report.duplicate_timestamps == 0
    assert report.out_of_session_rows == 0
    assert report.non_positive_prices == 0
    assert report.estimated_missing_minutes == 0
    assert report.is_clean


def test_out_of_session_rows_are_counted(tmp_path):
    """After-hours prints in a regular-session panel mean the collector's window is
    wrong, and every per-session statistic computed from it is off by those rows."""
    inside = [make_bar(index) for index in range(3)]
    after_close = make_bar(0, at=datetime(2025, 9, 1, 16, 0, tzinfo=clock.MARKET_TZ))
    weekend = make_bar(0, at=datetime(2025, 9, 6, 10, 0, tzinfo=clock.MARKET_TZ))
    report = analyze_bars("000660", inside + [after_close, weekend])
    assert report.out_of_session_rows == 2


def test_non_positive_prices_are_counted_even_though_bar_validation_forbids_them(tmp_path):
    """`Bar.model_construct` skips validation and is the obvious shortcut for a loader
    turning a million parquet rows into objects. The counter exists for that path."""
    bad = Bar.model_construct(
        symbol="000660",
        timestamp=FIRST_OPEN,
        interval_minutes=1,
        open=0.0,
        high=0.0,
        low=0.0,
        close=0.0,
        volume=0.0,
        traded_value=0.0,
    )
    report = analyze_bars("000660", [make_bar(1), bad])
    assert report.non_positive_prices == 1
    assert not report.is_clean


def test_the_expected_bars_per_day_constant_is_a_parameter_not_a_literal(tmp_path):
    """`391` appeared inline in four places in the system this replaces and was
    changed in two when the closing auction moved. The estimate must follow the
    parameter, not a number compiled into the checker."""
    bars = [make_bar(index) for index in range(100)]
    assert analyze_bars("000660", bars, expected_per_day=100).estimated_missing_minutes == 0
    assert analyze_bars("000660", bars, expected_per_day=140).estimated_missing_minutes == 40
    assert analyze_bars("000660", bars).estimated_missing_minutes == 391 - 100

    assert expected_bars_per_session(1) == 391
    assert expected_bars_per_session(5) == 78
    assert expected_bars_per_session(1, session_minutes=100) == 100
    with pytest.raises(ValueError):
        expected_bars_per_session(0)


def test_missing_minutes_are_counted_per_observed_day_only(tmp_path):
    """A day with no rows at all is invisible here, deliberately. Without a holiday
    calendar, 'no bars on this date' and 'the exchange was closed' are the same
    observation, and guessing inflates the count on every public holiday."""
    day_one = [make_bar(index) for index in range(10)]
    day_two = [
        make_bar(0, at=datetime(2025, 9, 2, 9, 0, tzinfo=clock.MARKET_TZ) + timedelta(minutes=offset))
        for offset in range(4)
    ]
    report = analyze_bars("000660", day_one + day_two, expected_per_day=10)
    assert report.estimated_missing_minutes == 6  # only day two is short


def test_an_empty_panel_is_reported_rather_than_treated_as_fine(tmp_path):
    """Zero rows for a symbol expected in the universe is usually a collection
    failure, and only the caller knows whether it was expected."""
    report = analyze_bars("000660", [])
    assert report.rows == 0
    assert report.warnings == ("no rows to analyse",)
    assert not report.is_clean


def test_rows_belonging_to_another_symbol_are_flagged(tmp_path):
    """A join that leaked another issue's bars into this panel is invisible to every
    downstream statistic, which will happily average the two together."""
    report = analyze_bars("000660", [make_bar(0), make_bar(1, symbol="000770")])
    assert any("different symbol" in warning for warning in report.warnings)


def test_the_shipped_panel_passes_its_own_quality_check(shipped):
    """End to end on the real artifact: no duplicates, no out-of-session rows, no
    non-positive prices. Missing minutes are expected — the generator seeds untraded
    minutes on purpose, because a loader that assumes density is a loader that reports
    a real gap as a data error."""
    bars = shipped.fetch_bars("SYN001", 1)
    report = analyze_bars("SYN001", bars)
    assert report.rows > 7_000
    assert report.duplicate_timestamps == 0
    assert report.out_of_session_rows == 0
    assert report.non_positive_prices == 0
    assert 0 < report.estimated_missing_minutes < report.rows


# ── the sample generator ─────────────────────────────────────────────────────
def test_the_shipped_sample_is_labelled_synthetic_in_its_own_filename():
    """The name, not only the README. A file copied out of `data/samples/` loses its
    README, and the next person to find it must be able to tell from the name alone
    that these are not market prices."""
    assert SAMPLE_FILE.is_file()
    assert "SYNTHETIC" in SAMPLE_FILE.name

    readme = SAMPLE_FILE.parent / "README.md"
    assert readme.is_file()
    text = readme.read_text(encoding="utf-8").lower()
    assert "not market data" in text
    assert "synthetic" in text
    assert "fictional" in text


def test_the_generator_is_deterministic_and_symbol_local():
    """Byte-for-byte reproducible, and adding a symbol does not move the existing
    ones — otherwise a regenerated sample invalidates every test baseline at once."""
    first = generate_bars(symbols=("SYN001",), sessions=2)
    again = generate_bars(symbols=("SYN001",), sessions=2)
    assert first == again

    with_neighbour = generate_bars(symbols=("SYN001", "SYN002"), sessions=2)
    assert [bar for bar in with_neighbour if bar.symbol == "SYN001"] == first

    assert generate_bars(symbols=("SYN001",), sessions=2, seed=7) != first


def test_generated_bars_are_session_local_and_ohlc_consistent():
    """Generated under `TZ=UTC` and asserted against KST, so a lost zone fails here
    rather than in a strategy three modules away."""
    bars = generate_bars(symbols=("SYN001",), sessions=3)
    assert {bar.symbol for bar in bars} == {"SYN001"}
    for bar in bars:
        assert bar.timestamp.tzinfo is not None
        assert clock.is_within_session(bar.timestamp)
        assert bar.low <= min(bar.open, bar.close)
        assert bar.high >= max(bar.open, bar.close)
        assert bar.open > 0 and bar.close > 0
    assert len(SYMBOLS) == 12


def test_writing_a_sample_produces_a_panel_the_replay_source_can_serve(tmp_path):
    """The generator and the reader are checked against each other, not against a
    schema written down twice."""
    out = tmp_path / "SYNTHETIC_minute_bars.parquet"
    path, rows = write_sample(out=out, readme=None, symbols=("SYN001", "SYN002"), sessions=2)
    assert path == out and rows > 0

    source = ReplayMarketDataSource(out)
    assert source.available_symbols() == ["SYN001", "SYN002"]
    assert len(source.fetch_bars("SYN001", 1)) + len(source.fetch_bars("SYN002", 1)) == rows


def test_the_store_and_the_replay_source_share_one_on_disk_schema(tmp_path):
    """A panel collected into the store must be replayable without a conversion step.
    A conversion that exists in only one direction is where column-name drift starts.
    """
    store = ParquetBarStore(tmp_path)
    written = generate_bars(symbols=("SYN001",), sessions=1)
    assert store.append_bars(written) == len(written)

    replayed = ReplayMarketDataSource(store.path_for("SYN001", 1)).fetch_bars("SYN001", 1)
    assert replayed == written


def test_a_zero_padded_issue_code_survives_a_csv_round_trip(tmp_path):
    """`Bar` keeps symbols as strings because newer KRX codes are not numeric and
    parsing to int drops leading zeros. A CSV reader defaults to exactly that parse:
    `000660` comes back as the integer 660, formats as "660", and joins against
    nothing. Every row is still present and every one is labelled with a code the
    exchange has never issued — which is why this needs a test rather than a comment.
    """
    original = make_bar(0, symbol="000660")
    path = tmp_path / "panel.csv"
    parquet_store.write_bar_frame_atomically(bars_to_frame([original]), path)
    assert "000660" in path.read_text(encoding="utf-8")

    replayed = ReplayMarketDataSource(path).fetch_bars("000660", 1)
    assert replayed == [original]
    assert replayed[0].symbol == "000660"


def test_a_panel_that_already_lost_its_leading_zeros_is_refused_not_repadded(tmp_path):
    """`660` is equally consistent with `000660` and `060660`. Padding to six would be
    a guess that reads as data, and the join it enables would silently address the
    wrong issue."""
    frame = bars_to_frame([make_bar(0, symbol="000660")])
    frame["symbol"] = [660]
    path = tmp_path / "numeric.parquet"
    frame.to_parquet(path, index=False)
    with pytest.raises(BarFrameError, match="will not be guessed"):
        parquet_store.read_bar_frame(path)


def test_a_staged_write_is_invisible_to_the_symbol_listing(tmp_path):
    """The temp suffix comes last (`symbol=X.parquet.<random>.tmp`) so the universe
    glob cannot see a half-written panel. Staging as `.tmp.parquet` would put a
    partial file into every downstream loop."""
    store = ParquetBarStore(tmp_path)
    store.append_bars([make_bar(0)])
    target = store.path_for("000660", 1)
    staged = parquet_store.stage_bar_frame(bars_to_frame([make_bar(1)]), target)
    try:
        assert store.symbols(1) == ["000660"]
    finally:
        os.unlink(staged)


def test_a_frame_missing_bar_columns_is_named_as_such(tmp_path):
    """The error says which columns are missing. A bare KeyError from pandas sends the
    reader looking for a bug in their own call."""
    path = tmp_path / "partial.csv"
    pd.DataFrame({"timestamp": ["2025-09-01T09:00:00+0900"], "close": [1.0]}).to_csv(
        path, index=False
    )
    with pytest.raises(BarFrameError, match="missing bar columns"):
        parquet_store.read_bar_frame(path)
