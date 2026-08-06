"""Hypothesis — a claimed edge with a stated mechanism.

A hypothesis is not a strategy. It is the prior claim that some observable
relationship exists; a strategy is the executable form that tries to harvest it.
Keeping them separate is what makes it possible to record the most common outcome
in this repository honestly: *the hypothesis survived and the strategy died*.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from ulid import ULID

from src.shared.domain.entities.evidence_basis import Basis
from src.shared.domain.entities.market import MarketCode


class EdgeMechanism(BaseModel):
    """Why the edge should exist at all, stated before the test is run.

    Required, and required *first*. A hypothesis with no mechanism can be fitted to
    anything, and the one confirmed strategy in this repository is confirmed partly
    because its mechanism is a committed corporate cash flow rather than a pattern.
    The rule learned the hard way: an edge you cannot explain will also disappear
    without explanation.
    """

    model_config = ConfigDict(frozen=True)

    statement: str
    """One sentence: who is on the other side, and why they keep taking that side."""

    counterparty: str | None = None
    """The participant whose systematic behaviour funds the edge. Every survivor
    here sits opposite Korean retail chase-buying flow; naming the counterparty is
    the cheapest available test of whether a mechanism is real or decorative."""

    basis: Basis = Basis.ASSUMED
    """Confidence in the mechanism *itself*, tracked separately from confidence in
    the statistics. These diverge constantly, and a strong t-statistic attached to an
    ASSUMED mechanism is the configuration that has failed most often here — an edge
    you cannot explain also disappears without explanation."""


class Hypothesis(BaseModel):
    """A claimed edge, registered before evidence is attached to it."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: ULID = Field(default_factory=ULID)
    """Unique identifier. ULID rather than UUID so that lexical sort equals
    registration order — which hypothesis was proposed before which is load-bearing
    when reconstructing whether a filter was chosen before or after seeing results."""

    key: str
    """Stable short slug used to deduplicate across search runs. The search
    consults the set of existing keys before re-testing, so a previously killed
    idea is never tested twice by accident. This is the mechanism
    that turns the graveyard from a document into a constraint."""

    name: str

    mechanism: EdgeMechanism

    market: MarketCode | None = None
    """None for hypotheses that are not about KRX equities — the ledger also holds
    Korean crypto arbitrage, Korea-stock perpetuals, and prediction-market entries."""

    signal: str | None = None
    """Name of the ranking or trigger variable, as referenced by the backtest
    engine. Validated against an allow-list of quantities known at the decision
    instant; anything outside it fails the look-ahead audit before any data is
    loaded."""

    registered_at: datetime | None = None
    """When the hypothesis entered the ledger. Compared against the timestamp of
    the data used to judge it — a rule may not be judged on the observations that
    produced it, and this pair of timestamps is how that is checked."""
