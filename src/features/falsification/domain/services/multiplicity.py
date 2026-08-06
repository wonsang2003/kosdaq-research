"""Multiplicity correction — the brake on an automated search.

The bar a candidate must clear rises with how much has been searched. Without this,
a loop that tests enough specifications will always produce something that looks
significant, and will report it with no memory of the 400 attempts behind it.

Three bars are provided because they answer different questions:

  `bonferroni_t`   — controls family-wise error. Conservative, defensible, the one
                     used for accept/reject.
  `noise_ceiling_t`— sqrt(2 ln k), the asymptotic expected maximum of k standard
                     normals. Not a test; a *sanity line*. A winning t sitting near
                     it is exactly what pure noise produces at that search width.
  `expected_max_t` — a softer version of the same idea, retained from the original
                     harness so historical ledger entries stay comparable.

The noise ceiling earns its place from a specific incident: a search across 92
hypotheses produced survivors whose t-statistics landed almost exactly on
sqrt(2 ln k) for their own grid sizes (k ~ 54-80 gives 2.82-2.96; observed 2.92).
Nothing in the accept/reject logic flagged it. The ceiling did.

Pure stdlib — `statistics.NormalDist` only, no SciPy. The harness has to run on a
reviewer's machine with no build toolchain.
"""

from __future__ import annotations

import math
from statistics import NormalDist

BASE_ALPHA = 0.05
MINIMUM_T = 2.0
"""Floor for the required bar. Even a single pre-registered test must clear |t| = 2;
the correction may only make the bar harder, never easier."""

_NORMAL = NormalDist()
_EULER = math.e


def bonferroni_t(n_tests: int, alpha: float = BASE_ALPHA) -> float:
    """Two-sided Bonferroni-adjusted normal quantile for `n_tests` comparisons.

    :param n_tests: number of hypotheses tested in the family. Values below 1 are
        clamped to 1.
    :return: the |t| a candidate must exceed to hold family-wise error at `alpha`.
    """
    n_tests = max(1, int(n_tests))
    q = min(1.0 - alpha / (2.0 * n_tests), 1 - 1e-12)
    return float(_NORMAL.inv_cdf(q))


def sidak_p(raw_p: float, n_tests: int) -> float:
    """Sidak-corrected p-value: the chance *any* of `n_tests` reaches this extreme.

    Slightly less conservative than Bonferroni and exact under independence. Used
    when reporting a specific cell that was picked out of a grid, because the
    honest question there is not 'is this cell significant' but 'would the best of
    k cells look this good by chance'.

    :param raw_p: uncorrected two-sided p-value of the selected cell.
    :param n_tests: number of cells the selection ranged over.
    :return: corrected p-value in [0, 1].
    """
    n_tests = max(1, int(n_tests))
    return 1.0 - (1.0 - raw_p) ** n_tests


def noise_ceiling_t(n_tests: int) -> float:
    """Asymptotic expected maximum of `n_tests` independent standard normals.

    :param n_tests: grid width the winner was selected from.
    :return: sqrt(2 ln k), the t-value pure noise is expected to reach at this
        search width. A result at or below this line carries no evidence, however
        small its nominal p-value.
    """
    return math.sqrt(2.0 * math.log(max(2, int(n_tests))))


def expected_max_t(n_tests: int) -> float:
    """Softer expected-maximum bar retained from the original harness.

    Kept unchanged so verdicts recorded under the old engine remain comparable to
    new ones; prefer `noise_ceiling_t` for new diagnostics.

    :return: the inverse normal CDF at 1 - 1/(k*e).
    """
    n_tests = max(1, int(n_tests))
    return float(_NORMAL.inv_cdf(1.0 - 1.0 / (n_tests * _EULER)))


def required_t(n_tests: int) -> float:
    """The bar an excess t-statistic must clear given the search performed so far.

    :param n_tests: cumulative count of hypotheses tested, from the ledger.
    :return: max(MINIMUM_T, Bonferroni bar).
    """
    return max(MINIMUM_T, bonferroni_t(n_tests))


def verdict_on_multiplicity(observed_t: float, n_tests: int) -> dict:
    """Full multiplicity report for one candidate.

    :param observed_t: the candidate's t-statistic, already day-clustered.
    :param n_tests: number of specifications the search ranged over.
    :return: dict with the required bar, the noise ceiling, and whether the
        candidate clears both. `above_noise_ceiling` false means the result is
        indistinguishable from the maximum of that many coin flips.
    """
    bar = required_t(n_tests)
    ceiling = noise_ceiling_t(n_tests)
    return {
        "observed_t": round(float(observed_t), 3),
        "n_tests": int(n_tests),
        "required_t": round(bar, 3),
        "noise_ceiling_t": round(ceiling, 3),
        "clears_bar": bool(abs(observed_t) >= bar),
        "above_noise_ceiling": bool(abs(observed_t) > ceiling),
        "pass": bool(abs(observed_t) >= bar and abs(observed_t) > ceiling),
    }
