"""Evidence basis — how a claim came to be believed.

Every non-obvious claim in this repository carries one of these four labels. The
labels exist because the failure mode that destroyed the most work here was not a
wrong number, it was a *correctly computed* number stacked on an unverified
premise. Five assumptions each 50% likely produce a 3% conclusion stated with
full confidence.
"""

from __future__ import annotations

from enum import Enum


class Basis(str, Enum):
    """Confidence class of a claim. Ordered weakest-last."""

    MEASURED = "F"
    """Computed in this repository from data that ships with it, or from a run
    whose output is recorded. Reproducible by a reader."""

    CITED = "C"
    """Taken from an external source. A source reference is mandatory; the claim
    inherits that source's own reliability, which may itself be an estimate."""

    ESTIMATED = "E"
    """A derivation or heuristic with stated reasoning but no direct measurement.
    Acceptable in a chain at most once, and never as the load-bearing link."""

    ASSUMED = "A"
    """Untested. Present so the assumption is visible rather than invisible. A
    quantitative conclusion may not be drawn through an ASSUMED link — the honest
    output at that point is a measurement plan, not a number."""

    @property
    def is_load_bearing(self) -> bool:
        """True when a conclusion may rest on this claim alone.

        MEASURED and CITED qualify. ESTIMATED and ASSUMED do not: they may appear
        in an argument, but a headline figure that depends on one must be reported
        as a range with the basis attached, or withheld.
        """
        return self in (Basis.MEASURED, Basis.CITED)
