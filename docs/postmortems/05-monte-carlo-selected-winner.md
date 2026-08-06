# Postmortem: a Monte Carlo that reported a zero probability of loss

**Verdict:** REFUTED · **Cause of death:** selection bias, bootstrapped ·
**The exhibit:** [`data/audit/monte_carlo_selected_winner.json`](../../data/audit/monte_carlo_selected_winner.json)

---

## The file contains its own control

This is why it ships. It has two arms, produced by the same simulator on the same data, differing
only in whether a post-hoc filter was applied to the trades before resampling.

| | mean | 5th pct | **P(loss)** | median max drawdown |
|---|---|---|---|---|
| `all_base` — unfiltered | +72.3% | **−98.3%** | **0.50** | **−84.9%** |
| `filtered_base` — post-hoc filter | **+2175.2%** | **+1938.9%** | **0.00** | −5.2% |

A reader needs no explanation. The delta between those two rows *is* the selection effect,
measured, in one file, by me, in June.

## What went wrong

The filter selected trades that had worked. The bootstrap then resampled from that selected set.
So the simulation was asking: *if I repeatedly draw from a bag of trades I chose for having been
profitable, how often do I lose?* The answer is zero, and the answer would be zero for any input
whatsoever. It measures the filter's hindsight and nothing about the strategy.

The specific defects, in order of how easy they are to catch:

1. **`loss_prob: 0.0`.** No strategy has a zero probability of loss. This alone should have ended
   the analysis. It is not an unusually good result — it is a result outside the range a correct
   calculation can produce, which makes it a message about the calculation.
2. **A 5th percentile of +1,939%.** Even the near-worst case is enormously positive. A distribution
   whose bad tail is spectacular is a distribution with no bad tail in it, because the bad
   outcomes were removed before sampling.
3. **The unfiltered arm was sitting directly above it, at `loss_prob: 0.50`.** The control was in
   the same file, in the same run, and I did not compare them.
4. **The resampler was unseeded**, so the run is not reproducible even as an artifact of the bug.

## The general shape

This is the same error as [postmortem 04](04-train-selection-hour-scan.md) wearing different
clothes. There, a configuration was selected on the evaluation fold and then evaluated on it.
Here, trades were selected for outcome and then had their outcome distribution estimated.

In both cases the machinery downstream is fine — the bootstrap is a correct bootstrap, the
walk-forward is a correct walk-forward. **The contamination is entirely upstream, in what was
allowed into the sample.** No amount of statistical sophistication downstream repairs it, which is
why this repository's battery puts the look-ahead audit and the fill-feasibility check *before*
any significance test rather than after.

## Why an unbelievable number is more useful than a wrong one

A backtest reporting +12% a year with a Sharpe of 1.4 could be right, could be overfit, and needs
work to tell apart. A backtest reporting a zero probability of loss needs no work at all: it is
reporting something that cannot happen, so the only question left is which upstream step broke.

The practical rule: **before checking whether a result is good, check whether it is possible.**
Range checks on the output — probability of loss strictly between 0 and 1, a bad tail that is
actually bad, a drawdown consistent with the volatility — are cheaper than any validation and
catch the errors that validation cannot see, because validation assumes the sample is honest.

## What would change my mind

The filtered arm becomes meaningful if the filter is applied **out of sample** — fit the filter on
one period, apply it unchanged to another, and bootstrap the second period's trades. That is a
different and legitimate experiment. It was never run, and the number in this file says nothing
about how it would turn out.

## The tests that pin this

[`tests/test_audit_artifacts.py`](../../tests/test_audit_artifacts.py):
`test_unfiltered_arm_is_a_coin_flip`,
`test_filtered_arm_claims_certainty_and_that_is_the_tell`,
`test_the_two_arms_differ_only_by_the_filter`.

## What I take from it

I generated this number, wrote it to disk, and left it in a results directory for five weeks
without looking at it hard enough to notice it was impossible. The failure was not the bug — bugs
are ordinary. The failure was that a headline result went unexamined because it was favourable,
and favourable results are exactly the ones that get the least scrutiny.

Everything in this repository that looks like excessive self-criticism is a correction for that.
