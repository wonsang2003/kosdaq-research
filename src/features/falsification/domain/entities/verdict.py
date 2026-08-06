"""Verdict — the outcome of putting a hypothesis through the falsification battery.

Design note that is the whole point of the type: a verdict carries `killed_by`,
never a score. Ranking strategies by a quality number is what produces the failure
this repository is built to avoid, because the number is optimisable and the search
will optimise it. Naming the test that killed a candidate is not optimisable.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from src.features.hypothesis.domain.entities.cause_of_death import CauseOfDeath


class Outcome(str, Enum):
    """Terminal state of a hypothesis."""

    CONFIRMED = "CONFIRMED"
    """Survived every gate, and the long-only net at realistic cost is positive.
    Three of roughly ninety-eight tested strategies reached this state."""

    REFUTED = "REFUTED"
    """A specific test failed and the failure is reproducible. The test is named in
    `killed_by`; the class is in `cause_of_death`."""

    UNDECIDED = "UNDECIDED"
    """Not refuted, not confirmed, and the deciding measurement is either still
    accumulating or does not exist in obtainable data. Kept distinct from REFUTED
    because collapsing the two is how a live question becomes a false negative."""

    RETIRED = "RETIRED"
    """Withdrawn without a clean refutation — usually because the sample can never
    reach significance within a usable horizon, or the instrument disappeared. The
    honest label when the answer is 'we stopped', not 'it failed'."""


class Verdict(BaseModel):
    """What the battery concluded, and which gate decided it."""

    model_config = ConfigDict(frozen=True)

    hypothesis_key: str

    outcome: Outcome

    survives_alpha: bool = False
    """Whether a market-neutral edge exists at all, judged before any cost is
    applied. Deliberately separate from `retail_tradable`: most entries in this
    ledger are True here and False there, and conflating them loses the single most
    repeated finding in the project."""

    retail_tradable: bool = False
    """Whether long-only net beats realistic all-in friction. Always evaluated at a
    fixed 0.38-0.40% round trip regardless of what cost a candidate's own spec
    proposes — an earlier revision of my search code lowered its declared cost
    to produce a tradable verdict, so the cost is no longer an
    input the candidate controls."""

    killed_by: list[str] = Field(default_factory=list)
    """Names of the gates that failed, in evaluation order. Empty iff the candidate
    survived. This is the field the ledger is queried on."""

    cause_of_death: CauseOfDeath | None = None
    """Taxonomy class for the decisive failure. None while UNDECIDED."""

    deflated_t_required: float | None = None
    """The significance bar this candidate actually had to clear, raised for the
    number of hypotheses tested so far. Stored per-verdict because it moves: the
    bar a candidate faced in test 12 is not the bar it would face in test 400, and
    a verdict without its contemporaneous bar cannot be re-read later."""

    @property
    def is_decided(self) -> bool:
        return self.outcome in (Outcome.CONFIRMED, Outcome.REFUTED)

    def __str__(self) -> str:
        if self.killed_by:
            return f"{self.hypothesis_key}: {self.outcome.value} <- {', '.join(self.killed_by)}"
        return f"{self.hypothesis_key}: {self.outcome.value}"
