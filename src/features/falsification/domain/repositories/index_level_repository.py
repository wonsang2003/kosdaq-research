"""Source of daily closing index levels."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping


class IndexLevelRepository(ABC):
    """Source of daily closing index levels.

    Deliberately an interface with no shipped live implementation. Resolving levels
    requires broker API credentials, which are out of scope for this repository;
    the analyses here run against recorded level maps. The abstraction is kept so
    the domain function above stays free of I/O and therefore testable.
    """

    @abstractmethod
    def levels(self) -> Mapping[str, Mapping[str, float]] | None:
        """
        :return: {board: {YYYYMMDD: close}} for KOSDAQ and KOSPI, or None if the
            levels cannot be resolved from any source including cache. Returning an
            empty mapping instead of None is a contract violation: the caller
            branches on None to mean 'not measurable', and {} would read as 'the
            market did nothing'.
        """
        raise NotImplementedError
