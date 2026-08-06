"""Domain errors.

These exist so that *missing* and *negative* stay distinguishable. The single most
expensive bug class in the parent trading system was a helper that returned a
plausible value instead of raising when it had insufficient data: a rate-limited
index lookup returned None, the caller's guard read `if value is None or value >
threshold: skip`, and a strategy that should have entered on a -6.12% session
logged 'condition not met' instead. Thirteen instances of the same shape were
found. Failing loudly is the fix.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base for every error raised by domain or infrastructure code here."""


class LedgerUnavailable(DomainError):
    """The hypothesis ledger could not be read or written.

    Deliberately not recoverable by returning an empty ledger: an empty ledger
    means 'nothing tested yet', which resets the multiplicity count to zero and
    lowers the significance bar to its most permissive value. A read failure that
    degrades into a permissive bar is worse than a crash.
    """


class DuplicateHypothesis(DomainError):
    """A hypothesis key already present in the ledger was registered again."""


class InsufficientData(DomainError):
    """Not enough observations to compute the requested statistic.

    Raised rather than returning NaN or zero. A zero here would be read downstream
    as a measured null, and a measured null is a much stronger claim than 'we could
    not measure'.
    """
