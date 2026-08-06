"""Structured logging that leaves a trace where somebody will actually look.

The original had one genuinely good idea buried in a lot of `print()`: any line that
looked like an error was *also* appended to a file the dashboard read. That mattered
because of how the incidents actually went. Stdout went to journald on a box nobody
was tailing at 09:03; the dashboard was the only surface anyone opened; so an
exception at the open produced a perfect log line that no human read for six days. A
log that is not read is not monitoring, it is exhaust.

So this keeps the idea and tightens it:

* Capture is decided by **level or pattern**, and the pattern check runs even for
  lines the level filter suppresses from stdout. A `WARNING: rate limited, skipping`
  under `min_level=ERROR` is exactly the trace an incident needs, and dropping it
  because somebody quietened the console is how the trace goes missing.
* Timestamps come from `clock.now()`, so every line is exchange-local and aware. The
  imitated system's log lines were naive UTC while its trading decisions were
  Seoul-local, and reconciling the two after the fact cost more than the incident.

**This module is the one place allowed to swallow a failure.** Everything else in
this repository raises rather than degrade — but a logger that raises converts a
diagnostic into an outage, and the caller is usually in an `except` block already.
Swallowing is bounded: nothing is silently lost, `dropped_events` counts every drop
and the reason goes to stderr, so a logger that has stopped recording is itself
visible rather than merely quiet.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from enum import Enum
from pathlib import Path
from typing import Any, TextIO

from src.features.operations.domain.entities.log_level import LogLevel
from src.features.operations.infrastructure.atomic_store import JsonlAppender
from src.shared.domain import clock

# `LogLevel` is imported for use *and* re-exported for callers that configure a
# logger. It is defined in `domain/entities/log_level.py`: severity is a vocabulary
# the ops stream is filtered and queried on, not a property of this writer.

LOG_EVENT_KIND = "log_event"
"""Discriminator, so log events and job outcomes can share one ops stream."""

DEFAULT_ERROR_PATTERNS: tuple[str, ...] = (
    r"error",
    r"exception",
    r"traceback",
    r"failed|failure",
    r"timed? ?out",
    r"rate[ _-]?limit",
    r"unavailable",
    r"refused|rejected",
    r"corrupt",
    r"kill switch",
    r"insufficient",
    r"undecidable",
)
"""Substrings that promote an otherwise ordinary line into the ops stream.

Chosen from the words that actually appeared in the log lines of this project's real
incidents. Over-capture is the cheap direction: a spurious record costs one row on a
dashboard, a missed one costs the incident timeline.
"""


class StructuredLogger:
    """Level-filtered console output plus selective capture to an ops stream."""

    def __init__(
        self,
        component: str,
        events_path: Path | str | None = None,
        stream: TextIO | None = None,
        min_level: LogLevel = LogLevel.INFO,
        capture_from: LogLevel = LogLevel.ERROR,
        error_patterns: Sequence[str] | None = None,
        stderr: TextIO | None = None,
    ) -> None:
        self.component = component
        self.min_level = min_level
        self.capture_from = capture_from
        self._events = JsonlAppender(events_path) if events_path is not None else None
        self._stream = stream if stream is not None else sys.stdout
        self._stderr = stderr if stderr is not None else sys.stderr
        patterns = (
            DEFAULT_ERROR_PATTERNS if error_patterns is None else tuple(error_patterns)
        )
        self._patterns = tuple(re.compile(p, re.IGNORECASE) for p in patterns)
        self.dropped_events = 0
        """Records the sinks refused. Non-zero means this logger is lying by omission
        and the number is the size of the lie."""

    # ── emit ─────────────────────────────────────────────────────────────────
    def log(self, level: LogLevel, message: str, **fields: Any) -> None:
        """Write one line and, if it looks like trouble, one structured record.

        Never raises. See the module docstring for why this module and no other gets
        that exemption.
        """
        try:
            moment = clock.now()
            rendered = _render_fields(fields)
            if level.rank >= self.min_level.rank:
                self._emit(
                    f"{moment.isoformat()} {level.value:<8} [{self.component}] "
                    f"{message}{(' ' + rendered) if rendered else ''}"
                )
            matched = self._matched_pattern(f"{message} {rendered}")
            if level.rank >= self.capture_from.rank or matched is not None:
                self._capture(moment, level, message, fields, matched)
        except BaseException as exc:  # noqa: BLE001 - logging must not become an outage
            self._panic(f"log call failed: {type(exc).__name__}: {exc}")

    def debug(self, message: str, **fields: Any) -> None:
        self.log(LogLevel.DEBUG, message, **fields)

    def info(self, message: str, **fields: Any) -> None:
        self.log(LogLevel.INFO, message, **fields)

    def warning(self, message: str, **fields: Any) -> None:
        self.log(LogLevel.WARNING, message, **fields)

    def error(self, message: str, **fields: Any) -> None:
        self.log(LogLevel.ERROR, message, **fields)

    def critical(self, message: str, **fields: Any) -> None:
        self.log(LogLevel.CRITICAL, message, **fields)

    def exception(self, message: str, exc: BaseException, **fields: Any) -> None:
        """Log a caught exception with its type preserved.

        The type is a separate field rather than string-formatted into the message,
        because `error_type` is what you group by when asking whether the same thing
        broke forty times or forty things broke once.
        """
        self.log(
            LogLevel.ERROR,
            message,
            error_type=type(exc).__name__,
            error=str(exc),
            **fields,
        )

    # ── capture ──────────────────────────────────────────────────────────────
    def _matched_pattern(self, text: str) -> str | None:
        for pattern in self._patterns:
            if pattern.search(text):
                return pattern.pattern
        return None

    def _capture(
        self,
        moment: Any,
        level: LogLevel,
        message: str,
        fields: Mapping[str, Any],
        matched: str | None,
    ) -> None:
        if self._events is None:
            return
        record = {
            "kind": LOG_EVENT_KIND,
            "ts": moment.isoformat(),
            "day": clock.day_key(moment),
            "level": level.value,
            "component": self.component,
            "message": message,
            "matched": matched or "",
            "fields": {key: _jsonable(value) for key, value in fields.items()},
        }
        try:
            self._events.append(record)
        except BaseException as exc:  # noqa: BLE001 - counted, not silent
            self._panic(f"ops event dropped: {type(exc).__name__}: {exc}")

    def _emit(self, line: str) -> None:
        try:
            print(line, file=self._stream, flush=True)
        except BaseException as exc:  # noqa: BLE001
            self._panic(f"console write failed: {type(exc).__name__}: {exc}")

    def _panic(self, message: str) -> None:
        """Last resort. Counts the loss so a mute logger is detectable."""
        self.dropped_events += 1
        try:
            print(f"logger[{self.component}]: {message}", file=self._stderr, flush=True)
        except BaseException:  # noqa: BLE001 - nowhere left to complain to
            pass

    # ── reading back ─────────────────────────────────────────────────────────
    def events(self) -> list[dict]:
        """Captured records, in write order. Empty when no stream is configured."""
        if self._events is None:
            return []
        return read_log_events(self._events.path)


def read_log_events(events_path: Path | str) -> list[dict]:
    """Log records from an ops stream that may also hold job outcomes."""
    return [
        record
        for record in JsonlAppender(events_path).read_all()
        if isinstance(record, dict) and record.get("kind") == LOG_EVENT_KIND
    ]


def _render_fields(fields: Mapping[str, Any]) -> str:
    return " ".join(f"{key}={_jsonable(value)!r}" for key, value in fields.items())


def _jsonable(value: Any) -> Any:
    """Coerce a field to something `json.dumps` accepts.

    A field that cannot be serialised must not take the log line down with it — the
    caller is frequently already handling an error, and losing that error to a
    serialisation failure of its own context is the worst possible trade.
    """
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)) or (
        isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray))
    ):
        return [_jsonable(item) for item in value]
    return repr(value)
