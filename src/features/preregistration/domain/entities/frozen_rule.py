"""Frozen rule — a specification committed before the data that will judge it exists.

Pre-registration is the only defence against the failure mode this repository is built
around: a search wide enough will always produce something that looks significant, and
the search leaves no trace in the result. Freezing the rule first converts "the best of
k cells" back into "one hypothesis".

Two invariants are enforced in code rather than documented, because both were violated
in practice before they were enforced:

  1. **A frozen rule cannot be mutated.** Adjusting a parameter after the freeze
     re-opens the search and re-inflates k, while leaving the recorded multiplicity
     count stale. The class raises instead.
  2. **There is no confirm line — only kill lines.** Forward testing at realistic event
     rates cannot reach significance inside a usable horizon, so a threshold that would
     "confirm" a rule cannot honestly be set. Offering the field at all invites someone
     to fill it in later.

The second invariant is why `JudgmentCriteria` has `kill_lines` and no counterpart.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from ulid import ULID

from src.shared.domain.errors import DomainError


class FrozenRuleViolation(DomainError):
    """An attempt to change a rule after it was frozen, or to judge it on its own data."""


class JudgmentCriteria(BaseModel):
    """How a frozen rule will be judged, committed at freeze time."""

    model_config = ConfigDict(frozen=True)

    horizon_sessions: int
    """Number of forward sessions after which the rule is judged. Fixed in advance so
    the observation window cannot be extended until the answer becomes favourable."""

    kill_thresholds: dict[int, float] = Field(default_factory=dict)
    """Sample size to the mean return at or below which the rule is abandoned.

    Kill lines only. Their *derivation* is publishable — set from the pre-freeze
    distribution at several sample sizes — but the values themselves are withheld for
    any live rule: a set of thresholds indexed by n is a sigma-over-root-n curve, and
    solving it against a published mean recovers the whole return distribution.
    """

    @property
    def confirm_threshold(self) -> None:
        """Always None. There is no confirm line, by construction.

        At the event rates involved, reaching a day-clustered t above 2 forward would
        take on the order of a thousand trades and several years. Forward testing here
        is a catastrophe detector, not a confirmation device, and a field that could
        hold a confirm threshold would eventually hold one.
        """
        return None


class FrozenRule(BaseModel):
    """A rule specification, sealed at a point in time."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    id: ULID = Field(default_factory=ULID)

    label: str

    filters: tuple[str, ...]
    """The filter expressions, in the order they are applied. A tuple rather than a list
    so the sequence cannot be appended to in place."""

    frozen_at: datetime
    """Freeze timestamp. Compared against the timestamp of any data used to judge the
    rule — a rule may not be judged on observations that predate its own freeze, because
    those are the observations that produced it."""

    search_width: int
    """Number of specifications the search ranged over before this one was frozen.

    Recorded at freeze time and never updated. This is the `k` that sets the
    significance bar, and it must include the cells abandoned early: a bar computed from
    an understated k is a bar the search has already cleared.
    """

    criteria: JudgmentCriteria

    def with_filter(self, _expression: str) -> FrozenRule:
        """Always raises. Present so the attempt fails loudly rather than silently.

        :raise FrozenRuleViolation: unconditionally.
        """
        raise FrozenRuleViolation(
            f"'{self.label}' was frozen at {self.frozen_at.isoformat()}. Adding a filter "
            f"re-opens a search of width {self.search_width} and invalidates the "
            f"significance bar derived from it. Freeze a new rule instead."
        )

    def assert_judgeable_on(self, observation_time: datetime) -> None:
        """Check that an observation postdates the freeze.

        :raise FrozenRuleViolation: if the observation predates the freeze, i.e. the rule
            would be judged on data that produced it.
        """
        if observation_time < self.frozen_at:
            raise FrozenRuleViolation(
                f"observation at {observation_time.isoformat()} predates the freeze at "
                f"{self.frozen_at.isoformat()} — this is the data that produced the rule"
            )
