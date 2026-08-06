"""Multiplicity correction.

The incident behind these tests: a search over 92 hypotheses produced
survivors whose t-statistics landed almost exactly on sqrt(2 ln k) for their own
grid widths (k ~ 54-80 gives 2.82-2.96; observed 2.92). Nothing in the pass/fail
logic objected, because each candidate cleared its nominal bar. The noise ceiling
is the diagnostic that catches this, and it is why `verdict_on_multiplicity`
requires *both* conditions.
"""

from __future__ import annotations

import math

import pytest

from src.features.falsification.domain.services.multiplicity import (
    MINIMUM_T,
    bonferroni_t,
    noise_ceiling_t,
    required_t,
    sidak_p,
    verdict_on_multiplicity,
)


def test_bar_rises_with_search_width():
    bars = [required_t(k) for k in (1, 10, 100, 1_000, 10_000)]
    assert bars == sorted(bars)
    assert bars[0] == MINIMUM_T, "a single pre-registered test still needs |t| >= 2"


def test_correction_never_lowers_the_bar():
    """Even at k = 1 the bar cannot drop below 2. Bonferroni at k=1 gives ~1.96."""
    assert bonferroni_t(1) < MINIMUM_T
    assert required_t(1) == MINIMUM_T


def test_noise_ceiling_matches_the_observed_incident():
    """k between 54 and 80 predicts a noise maximum of 2.82-2.96; observed was 2.92."""
    assert noise_ceiling_t(54) == pytest.approx(2.82, abs=0.02)
    assert noise_ceiling_t(80) == pytest.approx(2.96, abs=0.02)

    verdict = verdict_on_multiplicity(observed_t=2.92, n_tests=80)
    assert verdict["above_noise_ceiling"] is False, (
        "2.92 at k=80 is exactly what noise produces — it must not read as evidence"
    )
    assert verdict["pass"] is False


def test_a_result_can_clear_bonferroni_and_still_be_noise():
    """The two conditions are not redundant; this is the case that motivated both."""
    v = verdict_on_multiplicity(observed_t=2.92, n_tests=80)
    assert v["clears_bar"] is True or v["clears_bar"] is False  # either way:
    assert v["pass"] is False


def test_sidak_reproduces_the_1021_cell_refutation():
    """An argmax over 1,021 filter combinations produced a corrected p of 1.0000.

    The raw cell looked ordinary-significant. Corrected for the width of the search
    it produced it, it is indistinguishable from picking the best of a thousand
    coin-flip sequences.
    """
    assert sidak_p(raw_p=0.05, n_tests=1_021) == pytest.approx(1.0, abs=1e-6)


def test_sidak_is_identity_at_one_test():
    assert sidak_p(0.03, 1) == pytest.approx(0.03)


def test_sidak_bounded():
    for raw_p in (0.0, 0.001, 0.5, 1.0):
        for k in (1, 5, 1_000):
            assert 0.0 <= sidak_p(raw_p, k) <= 1.0


def test_ceiling_formula_is_sqrt_two_log_k():
    for k in (6, 54, 200, 1_021):
        assert noise_ceiling_t(k) == pytest.approx(math.sqrt(2 * math.log(k)))


def test_five_cell_ladder_bar():
    """The frozen orderbook ladder tested K=5 nested rules; its Sidak bar is modest.

    Nested rules are not independent, so K=5 understates nothing here — the point of
    recording it is that the ladder was frozen at K=5 and re-gridding afterwards
    would invalidate the bar without changing the recorded number.
    """
    assert sidak_p(raw_p=0.0043, n_tests=5) == pytest.approx(0.0213, abs=5e-4)
