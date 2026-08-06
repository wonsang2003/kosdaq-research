"""Repository interface for the hypothesis ledger."""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.features.falsification.domain.entities.verdict import Verdict
from src.features.hypothesis.domain.entities.hypothesis import Hypothesis


class HypothesisRepository(ABC):
    """Persistent record of every hypothesis tested, and why each one died.

    This interface exists because the original implementation hard-coded an
    absolute filesystem path inside the module that the battery imported, which
    made the harness unrunnable on any other machine and untestable without
    touching the real ledger. Storage is an infrastructure decision; the domain
    only needs these five questions answered.
    """

    @abstractmethod
    def find_by_key(self, key: str) -> Hypothesis | None:
        """
        :return: the Hypothesis with this key, or None if it was never registered.
        :raise LedgerUnavailable: if the backing store cannot be read.
        """
        raise NotImplementedError

    @abstractmethod
    def seen_keys(self) -> set[str]:
        """Keys of every hypothesis already tested.

        Consulted before a search spends compute, so a previously killed idea is
        never silently re-tested. An empty set and an unreadable ledger must not be
        indistinguishable — hence the raise below rather than a quiet fallback.

        :return: all registered keys; empty set if the ledger exists but is empty.
        :raise LedgerUnavailable: if the backing store cannot be read.
        """
        raise NotImplementedError

    @abstractmethod
    def count_tests(self) -> int:
        """Number of hypotheses tested to date.

        This is the `k` in the multiplicity correction. It must count *every* test,
        including the ones abandoned early, or the significance bar is understated
        and the search grades itself against a bar it has already passed.

        :return: total count of ledger entries.
        :raise LedgerUnavailable: if the backing store cannot be read.
        """
        raise NotImplementedError

    @abstractmethod
    def record(self, hypothesis: Hypothesis, verdict: Verdict) -> None:
        """Append a hypothesis and its verdict. Append-only by contract.

        Rewriting a past verdict destroys the ability to reconstruct which bar a
        candidate faced at the time it was judged.

        :raise LedgerUnavailable: if the backing store cannot be written.
        :raise DuplicateHypothesis: if `hypothesis.key` is already present.
        """
        raise NotImplementedError

    @abstractmethod
    def graveyard(self) -> list[Verdict]:
        """Every verdict that did not survive.

        :return: verdicts with `survives_alpha` false, in insertion order.
        :raise LedgerUnavailable: if the backing store cannot be read.
        """
        raise NotImplementedError
