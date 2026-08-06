"""The shipped hypothesis ledger, read through the repository interface.

The assertions on the real `data/results/ledger.json` are the repository's thesis
stated as a test: fifteen hypotheses were tested, seven cleared every alpha gate
with t-statistics up to 11.49, and **none of the seven is tradable by a retail
account after friction**. If a future edit to the ledger breaks that relationship,
these fail loudly rather than letting the README quietly become wrong.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.features.falsification.domain.entities.verdict import Outcome, Verdict
from src.features.hypothesis.domain.entities.cause_of_death import CauseOfDeath
from src.features.hypothesis.domain.entities.hypothesis import EdgeMechanism, Hypothesis
from src.features.hypothesis.infrastructure.json_hypothesis_repository import (
    JsonHypothesisRepository,
)
from src.shared.domain.errors import DuplicateHypothesis, LedgerUnavailable

LEDGER = Path(__file__).resolve().parents[1] / "data" / "results" / "ledger.json"


@pytest.fixture
def repo() -> JsonHypothesisRepository:
    return JsonHypothesisRepository(LEDGER)


# ── the thesis ───────────────────────────────────────────────────────────────
def test_ledger_has_fifteen_tested_hypotheses(repo):
    assert repo.count_tests() == 15


def test_seven_survive_the_alpha_gates(repo):
    assert len(repo.survivors()) == 7


def test_every_survivor_is_untradable_after_friction(repo):
    """The whole repository in one assertion.

    Real, market-neutral, statistically solid alpha — and not one instance of it
    reachable long-only once the 0.20% sell-side transaction tax is paid.
    """
    survivors = repo.survivors()
    assert survivors, "guard: the fixture must not be silently empty"
    assert all(not v.retail_tradable for v in survivors)
    assert all(v.cause_of_death is CauseOfDeath.COST_FLOOR for v in survivors)
    assert all(v.outcome is Outcome.REFUTED for v in survivors)


def test_no_hypothesis_is_confirmed(repo):
    """CONFIRMED requires surviving the gates *and* clearing cost. Nothing does."""
    all_verdicts = repo.graveyard() + repo.survivors()
    assert not [v for v in all_verdicts if v.outcome is Outcome.CONFIRMED]


def test_strongest_survivor_exceeds_t_of_eleven(repo):
    """Reads the raw file: the headline t is high, which is the point being made."""
    rows = json.loads(LEDGER.read_text())
    best = max(r["excess_t"] for r in rows if r.get("survives_alpha"))
    assert best > 11.0


def test_in_sample_strength_does_not_survive_out_of_sample(repo):
    """The first survivor: in-sample excess t = 5.12, out-of-sample t = -0.13."""
    rows = json.loads(LEDGER.read_text())
    row = next(r for r in rows if r["name"] == "turnover_top10")
    assert row["excess_t"] == pytest.approx(5.12, abs=0.01)
    assert row["oos"]["excess_t"] == pytest.approx(-0.13, abs=0.01)
    assert row["oos"]["excess_t"] < 0 < row["excess_t"]


def test_graveyard_causes_are_all_classified(repo):
    for verdict in repo.graveyard():
        assert verdict.cause_of_death is not None
        assert verdict.killed_by, "a refuted verdict must name the gate that killed it"


def test_seen_keys_covers_every_row(repo):
    assert len(repo.seen_keys()) == repo.count_tests()


# ── failure behaviour ────────────────────────────────────────────────────────
def test_missing_ledger_raises_instead_of_returning_empty(tmp_path):
    """An unreadable ledger must not degrade into 'nothing tested yet'.

    count_tests() feeds the multiplicity bar. If a read failure returned 0, the bar
    would drop to its most permissive value exactly when the system is least able
    to notice. This is the silent-failure shape that cost the parent system nine
    confirmed bugs.
    """
    repo = JsonHypothesisRepository(tmp_path / "nope.json")
    for call in (repo.count_tests, repo.seen_keys, repo.graveyard):
        with pytest.raises(LedgerUnavailable):
            call()


def test_corrupt_ledger_raises(tmp_path):
    bad = tmp_path / "ledger.json"
    bad.write_text("{not json")
    with pytest.raises(LedgerUnavailable):
        JsonHypothesisRepository(bad).count_tests()


def test_non_array_ledger_raises(tmp_path):
    bad = tmp_path / "ledger.json"
    bad.write_text('{"key": "x"}')
    with pytest.raises(LedgerUnavailable):
        JsonHypothesisRepository(bad).count_tests()


# ── writes ───────────────────────────────────────────────────────────────────
def _hypothesis(key: str) -> Hypothesis:
    return Hypothesis(
        key=key,
        name=key,
        signal="sig_test",
        mechanism=EdgeMechanism(statement="test double"),
    )


def test_record_appends_and_is_readable(tmp_path):
    path = tmp_path / "ledger.json"
    repo = JsonHypothesisRepository(path)
    verdict = Verdict(
        hypothesis_key="k1",
        outcome=Outcome.REFUTED,
        killed_by=["placebo"],
        cause_of_death=CauseOfDeath.NOTHING_THERE,
    )
    repo.record(_hypothesis("k1"), verdict)

    assert repo.count_tests() == 1
    assert repo.seen_keys() == {"k1"}


def test_record_rejects_duplicate_keys(tmp_path):
    """Append-only, and a key is tested once. Re-testing a dead idea is the waste
    the ledger exists to prevent."""
    path = tmp_path / "ledger.json"
    repo = JsonHypothesisRepository(path)
    v = Verdict(hypothesis_key="k1", outcome=Outcome.REFUTED, killed_by=["placebo"])
    repo.record(_hypothesis("k1"), v)
    with pytest.raises(DuplicateHypothesis):
        repo.record(_hypothesis("k1"), v)


def test_recording_does_not_touch_the_shipped_ledger(tmp_path, repo):
    before = LEDGER.read_bytes()
    JsonHypothesisRepository(tmp_path / "x.json").record(
        _hypothesis("scratch"),
        Verdict(hypothesis_key="scratch", outcome=Outcome.UNDECIDED),
    )
    assert LEDGER.read_bytes() == before
