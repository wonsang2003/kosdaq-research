"""The scanner autopsy, as assertions.

These turn the two headline findings of `docs/postmortems/03` and `04` from prose into
things that fail in CI if the shipped data stops supporting them. Both artifacts are my
own scanner's output from June, copied unmodified into `data/audit/`.

A claim in a README is rhetoric. The same claim as a passing assertion against shipped
data is checkable by a reader in one command, which is the only form of evidence this
repository considers worth much.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.replay_hour_scan import load_configs, pearson

AUDIT = Path(__file__).resolve().parents[1] / "data" / "audit"


# ── train-selection failure ──────────────────────────────────────────────────
def test_every_configuration_loses_money_out_of_sample():
    """All twelve. There was no hour to pick.

    This is the finding that retired the scanner. The positive training numbers were
    the spread of noise across twelve draws; the maximum of that spread is not a
    selection, it is the problem restated.
    """
    rows = load_configs()
    assert len(rows) == 12
    assert all(r["test"] < 0 for r in rows), (
        "if any config is positive out of sample the postmortem's headline is wrong"
    )


def test_train_selected_config_ranks_eighth_of_twelve_on_test():
    """Rank 1 on train, rank 8 on test — and it was written into the strategy."""
    rows = load_configs()
    by_train = sorted(rows, key=lambda r: -r["train"])
    by_test = sorted(rows, key=lambda r: -r["test"])

    picked = by_train[0]
    assert picked["label"] == "13"
    assert picked["train"] == pytest.approx(0.116, abs=0.001)
    assert picked["test"] == pytest.approx(-0.854, abs=0.001)

    test_rank = [r["label"] for r in by_test].index(picked["label"]) + 1
    assert test_rank == 8


def test_train_carries_almost_no_information_about_test():
    """Pearson ~0.3 across configurations: ranking on train was near-random.

    This is the number that makes the failure structural rather than unlucky. A
    procedure whose selection criterion correlates 0.3 with the objective is not a
    weak method, it is close to no method.
    """
    rows = load_configs()
    corr = pearson([r["train"] for r in rows], [r["test"] for r in rows])
    assert corr == pytest.approx(0.296, abs=0.005)
    assert corr < 0.35


# ── selection effect measured in one file ────────────────────────────────────
def _mc() -> dict:
    return json.loads((AUDIT / "monte_carlo_selected_winner.json").read_text())


def test_unfiltered_arm_is_a_coin_flip():
    """Before the post-hoc filter: 50% chance of loss, −98% at the 5th percentile."""
    arm = _mc()["all_base"]
    assert arm["loss_prob"] == pytest.approx(0.50)
    assert arm["p05"] < -95
    assert arm["mdd_median"] < -80


def test_filtered_arm_claims_certainty_and_that_is_the_tell():
    """After selecting the winning trades: `loss_prob` 0.0 and a +2175% mean.

    No strategy has a zero probability of loss. The number is not a result, it is a
    receipt for the selection: the bootstrap resamples trades that were chosen for
    having worked, so it measures the filter's hindsight and nothing else.
    """
    arm = _mc()["filtered_base"]
    assert arm["loss_prob"] == 0.0
    assert arm["mean"] > 2000
    assert arm["p05"] > 0, "even the 5th percentile is hugely positive — impossible"


def test_the_two_arms_differ_only_by_the_filter():
    """Same simulator, same data, one post-hoc filter. The delta is the selection effect.

    Shipping both arms is the whole exhibit: a reader does not need the argument
    explained, because the file contains its own control.
    """
    mc = _mc()
    assert set(mc) == {"all_base", "filtered_base"}
    assert mc["all_base"]["loss_prob"] > mc["filtered_base"]["loss_prob"]
    assert mc["filtered_base"]["mean"] > 25 * mc["all_base"]["mean"]
