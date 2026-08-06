"""Atomic JSON state, in one place.

The system this imitates knew the correct pattern and applied it inconsistently: the
broker adapter used `flock` + write-to-temp + `os.replace` for its position file,
while the main service used a plain `json.dump(d, open(path, "w"))` for state that
mattered just as much. A crash between truncate and write leaves a zero-length file
where a position used to be, and the next read either raises or — worse — returns
an empty dict that reads as "no positions".

There is one implementation here and everything uses it. Duplicated persistence
logic diverges; that is the same failure this repository documents elsewhere at the
level of statistics.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

from src.shared.domain.errors import DomainError


class StateUnavailable(DomainError):
    """State could not be read or written.

    Not recoverable by substituting an empty value. An empty dict is a *claim* that
    there is no state, and acting on that claim can mean re-entering a position that
    is already open.
    """


class AtomicJsonStore:
    """Crash-safe JSON document store.

    Writes go to a temporary file in the same directory and are moved into place with
    `os.replace`, which is atomic on POSIX within a filesystem. A reader therefore
    sees either the previous document or the new one, never a partial one.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._lock_path = self._path.with_suffix(self._path.suffix + ".lock")

    @property
    def path(self) -> Path:
        return self._path

    def load(self, default: Any = None) -> Any:
        """Read the document.

        :param default: returned only when the file does **not exist**. A file that
            exists but cannot be parsed raises instead, because unparseable state is
            corruption and silently replacing it with a default destroys evidence.
        :raise StateUnavailable: on read or parse failure of an existing file.
        """
        if not self._path.exists():
            return {} if default is None else default
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StateUnavailable(f"unreadable state: {self._path}") from exc

    def save(self, document: Any) -> None:
        """Replace the document atomically.

        :raise StateUnavailable: if the write or the rename fails.
        """
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                dir=str(self._path.parent), prefix=self._path.name + ".", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(document, handle, ensure_ascii=False, indent=1)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, self._path)
            except BaseException:
                # Includes KeyboardInterrupt and SystemExit — a signal arriving here
                # must not leave a stray temp file next to real state.
                with suppress(OSError):
                    os.unlink(tmp)
                raise
        except OSError as exc:
            raise StateUnavailable(f"unwritable state: {self._path}") from exc

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Exclusive advisory lock for the duration of the block.

        Needed for read-modify-write. Two processes each doing load → mutate → save
        without this will silently lose one of the two mutations, and the loss is
        invisible because both writes succeed.
        """
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._lock_path, "w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def mutate(self, change: Callable[[Any], Any], default: Any = None) -> Any:
        """Locked read-modify-write.

        :param change: receives the current document and returns the replacement.
        :return: the document that was written.
        :raise StateUnavailable: on any read or write failure; the lock is released.
        """
        with self.locked():
            current = self.load(default)
            updated = change(current)
            self.save(updated)
            return updated


class JsonlAppender:
    """Append-only line store, for ledgers and event streams.

    Append is the right shape for anything that is a record of what happened: it is
    cheap, it never rewrites history, and a truncated final line loses one record
    instead of the file. `flush` + `fsync` on every append because the records this
    holds are the only evidence that an order was placed.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def append(self, record: dict) -> None:
        """Append one JSON record.

        :raise StateUnavailable: if the record cannot be written. Deliberately not
            swallowed: a journal that silently drops writes is worse than no journal,
            because downstream code trusts it for state recovery.
        """
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except (OSError, TypeError) as exc:
            raise StateUnavailable(f"unwritable journal: {self._path}") from exc

    def read_all(self) -> list[dict]:
        """Every record, in write order.

        Malformed trailing lines are skipped — a process killed mid-append leaves a
        partial final line, and that is an expected condition rather than corruption.
        A malformed line anywhere *else* raises, because that is corruption.

        :raise StateUnavailable: if the file exists but cannot be read, or a
            non-final line is unparseable.
        """
        if not self._path.exists():
            return []
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise StateUnavailable(f"unreadable journal: {self._path}") from exc

        records: list[dict] = []
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                if index == len(lines) - 1:
                    break  # torn final line from an interrupted append
                raise StateUnavailable(
                    f"corrupt journal line {index + 1}: {self._path}"
                ) from exc
        return records
