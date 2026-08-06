"""Severity of an operational log line.

An entity rather than a logging-library detail, because the level is the field the
ops stream is *filtered and queried on* after the fact, and `ops_alerts` reasons over
it without knowing how a line was written. Keeping the vocabulary in the domain is
what lets the capture rule ("promote anything at or above ERROR") be stated once and
enforced by both the writer and the reader.

`rank` exists because `str, Enum` compares lexically — `"WARNING" > "ERROR"` is True
as a string and false as a severity — so every comparison in this codebase goes
through the explicit ordering below rather than through the member value.
"""

from __future__ import annotations

from enum import Enum


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        return _RANKS[self]


_RANKS = {
    LogLevel.DEBUG: 10,
    LogLevel.INFO: 20,
    LogLevel.WARNING: 30,
    LogLevel.ERROR: 40,
    LogLevel.CRITICAL: 50,
}
