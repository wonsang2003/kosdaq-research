"""Parquet-backed bar storage, and the on-disk bar schema everything else reads.

The one requirement that drove this file: **append must never truncate.**

The implementation being replaced kept a symbol's whole history in memory and, on
every save, opened the target with mode `"w"` and rewrote it. That is a truncate
followed by a write, and the gap between the two is real. A SIGTERM arriving in that
gap — a deploy, an OOM kill, a laptop lid — left a zero-length file where eleven
years of minute bars used to be. The loss is silent: the next collection run reads
zero rows, decides it is starting fresh, and re-collects from the current day. What
survives on disk then looks like a symbol that only started trading last Tuesday.

Every write here goes to a temporary file in the destination directory and is moved
into place with `os.replace`, which is atomic on POSIX within one filesystem. A
reader concurrent with a writer therefore sees either the whole previous file or the
whole new one. This is the same pattern as
`operations/infrastructure/atomic_store.py`, and the advisory lock used for
read-modify-write is borrowed from it rather than reimplemented — duplicated
persistence logic is exactly what let the original diverge into two behaviours.

The frame helpers are public because the replay source reads the same schema this
store writes. That is deliberate: a panel collected into this store must be
replayable without a conversion step, and a conversion step that exists in only one
direction is where column-name drift starts.
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Sequence
from contextlib import suppress
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.features.market_data.domain.entities.bar import Bar
from src.features.market_data.domain.repositories.market_data import BarStore
from src.features.operations.infrastructure.atomic_store import (
    AtomicJsonStore,
    StateUnavailable,
)
from src.shared.domain import clock

BAR_COLUMNS: tuple[str, ...] = (
    "symbol",
    "timestamp",
    "interval_minutes",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "traded_value",
)
"""Column order of the on-disk schema. Stated once so the store and the replay source
cannot disagree about it."""

_SAFE_SYMBOL = re.compile(r"[A-Za-z0-9_-]{1,32}")
"""Symbols become path components. Anything outside this set is refused rather than
sanitised, because a silently rewritten symbol writes one issue's bars into another
issue's file and nothing downstream can detect the swap."""

_HAS_UTC_OFFSET = r"(?:Z|[+-]\d{2}:?\d{2})$"
"""A textual timestamp without this suffix carries no zone, and this module refuses to
invent one for it."""


class BarFrameError(ValueError):
    """A file was readable but is not a bar panel.

    A `ValueError` subclass so callers that already funnel `ValueError` into their own
    domain error (`StateUnavailable` here, `MarketDataUnavailable` in the replay
    source) pick it up without a special case.
    """


# ── schema helpers ───────────────────────────────────────────────────────────
def empty_bar_frame() -> pd.DataFrame:
    """A zero-row frame with the exact dtypes of a populated one.

    Dtypes matter on an empty frame: concatenating a default-dtype empty frame onto a
    timezone-aware one drops the timezone and turns every stored timestamp naive,
    which is the failure `clock` exists to prevent, arriving through pandas instead of
    through `datetime.now()`.
    """
    return pd.DataFrame(
        {
            "symbol": pd.Series(dtype="object"),
            "timestamp": pd.Series(dtype=pd.DatetimeTZDtype(tz=clock.MARKET_TZ)),
            "interval_minutes": pd.Series(dtype="int64"),
            "open": pd.Series(dtype="float64"),
            "high": pd.Series(dtype="float64"),
            "low": pd.Series(dtype="float64"),
            "close": pd.Series(dtype="float64"),
            "volume": pd.Series(dtype="float64"),
            "traded_value": pd.Series(dtype="float64"),
        }
    )


def bars_to_frame(bars: Sequence[Bar]) -> pd.DataFrame:
    """Bars to the on-disk schema, timestamps normalised to `MARKET_TZ`.

    Normalising the zone on the way in rather than on the way out means two bars for
    the same instant expressed in different zones deduplicate against each other. A
    collector that switches from a UTC-stamped endpoint to a KST-stamped one would
    otherwise double every row in the overlap.
    """
    if not bars:
        return empty_bar_frame()
    stamps = pd.to_datetime([bar.timestamp for bar in bars], utc=True).tz_convert(
        clock.MARKET_TZ
    )
    return pd.DataFrame(
        {
            "symbol": [bar.symbol for bar in bars],
            "timestamp": stamps,
            "interval_minutes": [int(bar.interval_minutes) for bar in bars],
            "open": [float(bar.open) for bar in bars],
            "high": [float(bar.high) for bar in bars],
            "low": [float(bar.low) for bar in bars],
            "close": [float(bar.close) for bar in bars],
            "volume": [float(bar.volume) for bar in bars],
            "traded_value": [float(bar.traded_value) for bar in bars],
        },
        columns=list(BAR_COLUMNS),
    )


def frame_to_bars(frame: pd.DataFrame) -> list[Bar]:
    """The on-disk schema back to validated `Bar` objects.

    Validation is deliberately not skipped for speed. A stored row with a zero price
    or an inverted high/low is corruption, and the cheapest place to find corruption
    is at the boundary — the alternative is a statistic computed from it three modules
    later, which is how this project once shipped a fabricated result.

    :raise BarFrameError: if the frame does not carry the expected columns.
    :raise ValueError: from pydantic, if a row is not a valid bar.
    """
    _require_columns(frame)
    if frame.empty:
        return []
    stamps = _aware_timestamps(frame["timestamp"])
    return [
        Bar(
            symbol=str(row.symbol),
            timestamp=stamp.to_pydatetime(),
            interval_minutes=int(row.interval_minutes),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
            traded_value=float(row.traded_value),
        )
        for row, stamp in zip(frame.itertuples(index=False), stamps, strict=True)
    ]


def read_bar_frame(path: Path | str, columns: Sequence[str] | None = None) -> pd.DataFrame:
    """Read a `.parquet` or `.csv` bar panel with its timezone checked.

    :param columns: subset to read; `timestamp` is always included because the
        timezone check is the point of this function.
    :raise FileNotFoundError: if the file is absent. Absence is the caller's to
        interpret — a missing store file is an empty store, a missing replay file is a
        broken deployment, and this function must not decide which.
    :raise BarFrameError: if the file is not a bar panel, or its timestamps are naive.
    :raise OSError: on I/O failure.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(str(path))

    wanted = None if columns is None else sorted({*columns, "timestamp"}, key=BAR_COLUMNS.index)
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        frame = pd.read_parquet(path, columns=wanted)
    elif suffix == ".csv":
        # `dtype` on the symbol column is not a nicety. KRX issue codes are
        # zero-padded — `000660` — and pandas reads that as the integer 660, which
        # then formats back as "660" and joins against nothing. The corruption is
        # total and completely silent: every row is still there, and every one of
        # them is now labelled with a code the exchange has never issued.
        frame = pd.read_csv(path, usecols=wanted, dtype=_csv_dtypes(wanted))
    else:
        raise BarFrameError(f"unsupported bar file format: {path.suffix or '(none)'}")

    missing = [name for name in (wanted or BAR_COLUMNS) if name not in frame.columns]
    if missing:
        raise BarFrameError(f"{path} is missing bar columns: {', '.join(missing)}")

    frame = frame.copy()
    frame["timestamp"] = _aware_timestamps(frame["timestamp"])
    _require_textual_symbols(frame, path)
    return frame


def _csv_dtypes(wanted: Sequence[str] | None) -> dict[str, type]:
    if wanted is not None and "symbol" not in wanted:
        return {}
    return {"symbol": str}


def _require_textual_symbols(frame: pd.DataFrame, path: Path) -> None:
    """Refuse a numeric symbol column instead of stringifying it.

    A file written by another tool can hold the symbol as an integer, at which point
    the zero-padding is already gone and cannot be recovered: `660` is equally
    consistent with `000660` and `060660`. Padding to six characters would be a guess
    that reads as data, and every join downstream would succeed against the wrong
    issue. Refusing sends the reader back to whatever wrote the file, which is where
    the fix belongs.
    """
    if "symbol" not in frame.columns or frame.empty:
        return
    if pd.api.types.is_numeric_dtype(frame["symbol"]):
        raise BarFrameError(
            f"{path} stores symbols as numbers; zero-padded issue codes cannot be "
            "recovered from an integer and will not be guessed"
        )
    frame["symbol"] = frame["symbol"].astype("object")


def write_bar_frame_atomically(frame: pd.DataFrame, path: Path | str) -> None:
    """Replace a bar file in one indivisible step.

    :raise OSError: if the write or the rename fails.
    """
    path = Path(path)
    tmp = stage_bar_frame(frame, path)
    try:
        os.replace(tmp, path)
    except BaseException:
        with suppress(OSError):
            os.unlink(tmp)
        raise
    _fsync_dir(path.parent)


def stage_bar_frame(frame: pd.DataFrame, target: Path) -> str:
    """Write `frame` to a sibling temp file and return its path, without publishing it.

    Split out from the rename so a multi-symbol batch can be fully written before any
    of it becomes visible; see `ParquetBarStore.append_bars`.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(
        dir=str(target.parent), prefix=target.name + ".", suffix=".tmp"
    )
    os.close(handle)
    try:
        # `mkstemp` creates 0600. A panel that arrives through this path is often a
        # repository artifact or a file another service reads, and inheriting the
        # secrecy of a temp file makes it unreadable for reasons no error message
        # connects to the write that produced it.
        os.chmod(tmp, 0o666 & ~_umask())
        ordered = frame.reindex(columns=list(BAR_COLUMNS))
        if target.suffix.lower() == ".csv":
            ordered.to_csv(tmp, index=False, date_format="%Y-%m-%dT%H:%M:%S%z")
        else:
            ordered.to_parquet(tmp, index=False)
        with open(tmp, "rb") as fsync_handle:
            os.fsync(fsync_handle.fileno())
    except BaseException:
        # Includes KeyboardInterrupt and SystemExit: a signal arriving mid-write must
        # not leave a stray `.tmp` next to real history, where the next glob finds it.
        with suppress(OSError):
            os.unlink(tmp)
        raise
    return tmp


def _umask() -> int:
    """Read the process umask without leaving it changed.

    `os.umask` has no getter; the only way to read it is to set it and set it back.
    Kept in one function so the two-call sequence cannot be interrupted by an early
    return added later.
    """
    current = os.umask(0o022)
    os.umask(current)
    return current


def _require_columns(frame: pd.DataFrame) -> None:
    missing = [name for name in BAR_COLUMNS if name not in frame.columns]
    if missing:
        raise BarFrameError(f"frame is missing bar columns: {', '.join(missing)}")


def _aware_timestamps(raw: pd.Series) -> pd.Series:
    """Coerce a timestamp column to `MARKET_TZ`, refusing anything naive.

    A naive column is refused rather than assumed to be exchange-local. Assuming is
    how a panel collected in a UTC container gets read back nine hours off — every
    session filter still passes, every bar is simply attributed to the wrong minute,
    and nothing in the pipeline can notice.
    """
    if isinstance(raw.dtype, pd.DatetimeTZDtype):
        return raw.dt.tz_convert(clock.MARKET_TZ)
    if pd.api.types.is_datetime64_any_dtype(raw):
        raise BarFrameError(
            "bar timestamps are timezone-naive; refusing to guess the exchange zone"
        )
    text = raw.astype("string")
    if len(text) and not text.str.contains(_HAS_UTC_OFFSET, regex=True, na=False).all():
        raise BarFrameError(
            "bar timestamps carry no UTC offset; refusing to guess the exchange zone"
        )
    return pd.to_datetime(raw, utc=True).dt.tz_convert(clock.MARKET_TZ)


def _fsync_dir(directory: Path) -> None:
    """Make a rename durable, not just visible.

    `os.replace` is atomic but the directory entry can still be lost to a power cut.
    Suppressed on failure because some filesystems refuse `O_RDONLY` fsync on a
    directory, and that is not a reason to fail a write that already succeeded.
    """
    with suppress(OSError):
        handle = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(handle)
        finally:
            os.close(handle)


# ── the store ────────────────────────────────────────────────────────────────
class ParquetBarStore(BarStore):
    """One parquet file per (interval, symbol), under a Hive-style directory layout.

    Partitioning by symbol is what keeps an append bounded: rewriting one symbol's
    file costs its own history, not the universe's. Partitioning by interval as well
    means a one-minute panel and a daily panel of the same issue never share a file,
    so re-deriving one cannot corrupt the other.

    Each file also carries its own `symbol` and `interval_minutes` columns even though
    both are in its path. Redundant on purpose: a parquet file that is copied out of
    the tree keeps its meaning, and a path-only convention silently loses it.
    """

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)
        self._guard = AtomicJsonStore(self._root / "_append")

    @property
    def root(self) -> Path:
        return self._root

    def path_for(self, symbol: str, interval_minutes: int) -> Path:
        """Where one symbol's bars at one interval live."""
        _validate_symbol(symbol)
        if interval_minutes <= 0:
            raise ValueError(f"interval_minutes must be positive, got {interval_minutes}")
        return (
            self._root
            / f"interval_minutes={int(interval_minutes)}"
            / f"symbol={symbol}.parquet"
        )

    def append_bars(self, bars: list[Bar]) -> int:
        """Merge bars into the store atomically, replacing same-timestamp rows.

        The whole batch is written to temp files first and only then renamed into
        place. Renames are cheap and cannot fail for lack of space, so the window in
        which a multi-symbol batch is half-visible is a few syscalls wide rather than
        as long as it takes to serialise a decade of bars. It is not zero — POSIX
        offers no multi-file rename — and it does not need to be, because this
        operation is idempotent: a retry after a partial commit converges on the same
        state and reports the rows the first attempt did not manage to add.

        :return: rows whose (symbol, interval, timestamp) was not already stored.
        :raise StateUnavailable: on any write failure, with nothing published.
        """
        if not bars:
            return 0

        groups: dict[tuple[str, int], list[Bar]] = {}
        for bar in bars:
            _validate_symbol(bar.symbol)
            groups.setdefault((bar.symbol, bar.interval_minutes), []).append(bar)

        staged: list[tuple[str, Path]] = []
        touched: set[Path] = set()
        added = 0
        try:
            with self._guard.locked():
                for (symbol, interval), group in sorted(groups.items()):
                    target = self.path_for(symbol, interval)
                    touched.add(target.parent)
                    existing = self._read_existing(target)
                    incoming = bars_to_frame(group).drop_duplicates(
                        subset="timestamp", keep="last"
                    )
                    known = set(existing["timestamp"])
                    added += int(
                        sum(1 for stamp in incoming["timestamp"] if stamp not in known)
                    )
                    superseded = existing["timestamp"].isin(set(incoming["timestamp"]))
                    pieces = [
                        piece
                        for piece in (existing[~superseded], incoming)
                        if not piece.empty
                    ]
                    merged = (
                        pd.concat(pieces, ignore_index=True)
                        if pieces
                        else empty_bar_frame()
                    )
                    merged = merged.sort_values(
                        "timestamp", kind="stable", ignore_index=True
                    )
                    staged.append((stage_bar_frame(merged, target), target))

                for tmp, target in staged:
                    os.replace(tmp, target)
                staged = []
                for directory in touched:
                    _fsync_dir(directory)
        except (OSError, ValueError, KeyError) as exc:
            raise StateUnavailable(f"could not append bars under {self._root}") from exc
        finally:
            for tmp, _ in staged:
                with suppress(OSError):
                    os.unlink(tmp)
        return added

    def load_bars(
        self,
        symbol: str,
        interval_minutes: int,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[Bar]:
        """Stored bars for one symbol, ascending, bounds inclusive.

        :raise StateUnavailable: if a stored file exists but cannot be read. Note this
            does **not** collapse into an empty list — "nothing stored" and "stored but
            unreadable" are different answers and a caller acting on the wrong one
            re-collects a history it already has, or worse, trusts a hole.
        """
        target = self.path_for(symbol, interval_minutes)
        if not target.exists():
            return []
        try:
            frame = read_bar_frame(target)
            frame = _within(frame, since, until)
            frame = frame.drop_duplicates(subset="timestamp", keep="last")
            frame = frame.sort_values("timestamp", kind="stable", ignore_index=True)
            return frame_to_bars(frame)
        except (OSError, ValueError, KeyError) as exc:
            raise StateUnavailable(f"unreadable bar file: {target}") from exc

    def symbols(self, interval_minutes: int) -> list[str]:
        """Every symbol stored at this interval, sorted."""
        if interval_minutes <= 0:
            raise ValueError(f"interval_minutes must be positive, got {interval_minutes}")
        directory = self._root / f"interval_minutes={int(interval_minutes)}"
        if not directory.is_dir():
            return []
        # The glob ends at `.parquet`, so a staged write — named
        # `symbol=X.parquet.<random>.tmp` — is invisible here. That is the reason the
        # temp suffix comes last rather than the file being staged as `.tmp.parquet`:
        # a half-written panel must never appear in the universe.
        return sorted(
            path.stem[len("symbol=") :] for path in directory.glob("symbol=*.parquet")
        )

    def last_timestamp(self, symbol: str, interval_minutes: int) -> datetime | None:
        """Newest stored bar time, or None when nothing is stored.

        Reads only the timestamp column. This is called once per symbol on every
        collection restart, and materialising a decade of OHLCV to answer "where did I
        stop" is the kind of cost that turns a resumable collector into one nobody
        restarts.

        :raise StateUnavailable: if the file exists but cannot be read — see
            `load_bars` for why that is not an empty answer.
        """
        target = self.path_for(symbol, interval_minutes)
        if not target.exists():
            return None
        try:
            frame = read_bar_frame(target, columns=["timestamp"])
        except (OSError, ValueError, KeyError) as exc:
            raise StateUnavailable(f"unreadable bar file: {target}") from exc
        if frame.empty:
            return None
        return frame["timestamp"].max().to_pydatetime()

    def _read_existing(self, target: Path) -> pd.DataFrame:
        if not target.exists():
            return empty_bar_frame()
        return read_bar_frame(target)


def _validate_symbol(symbol: str) -> None:
    if not _SAFE_SYMBOL.fullmatch(symbol or ""):
        raise ValueError(
            f"refusing {symbol!r} as a storage key: symbols become path components"
        )


def _within(
    frame: pd.DataFrame, since: datetime | None, until: datetime | None
) -> pd.DataFrame:
    """Inclusive window filter.

    :raise BarFrameError: if a naive bound is supplied. Comparing a naive bound with
        an aware column raises inside pandas with a message about nothing in
        particular; refusing here names the actual mistake.
    """
    for label, bound in (("since", since), ("until", until)):
        if bound is not None and bound.tzinfo is None:
            raise BarFrameError(f"{label} must be timezone-aware")
    if since is not None:
        frame = frame[frame["timestamp"] >= pd.Timestamp(since)]
    if until is not None:
        frame = frame[frame["timestamp"] <= pd.Timestamp(until)]
    return frame
