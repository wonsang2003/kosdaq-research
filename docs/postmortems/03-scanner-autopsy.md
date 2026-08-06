# Postmortem: 55% of my own score was a constant

**Subject:** a 6,914-line intraday scanner I wrote in June · **Verdict:** retired ·
**Found by:** auditing code I had already stopped running

---

## What it was

A rule-based intraday ranking engine for KOSDAQ small and mid caps. It ingested minute bars from a
Korean broker's REST API, computed value-area and turnover features, blended eight sub-scores into
a composite, ranked the universe, and emitted candidates. Around forty CLI subcommands covering
collection, backtest, walk-forward validation, an ML layer, and a paper-trading runner. It ran a
lot: 1,839 report files, a trained model, several gigabytes of collected market data.

The composite score was a fixed-weight linear blend:

| Sub-score | Weight |
|---|---|
| market regime | 0.15 |
| theme strength | 0.20 |
| turnover | 0.15 |
| flow | 0.15 |
| chart / value area | 0.15 |
| state machine | 0.10 |
| lead-lag | 0.05 |
| risk | 0.05 |

## What it actually was

Five of those eight were constants on real data. Not noisy, not weakly informative — literally the
same number for every symbol on every bar.

**Flow (0.15).** The feature builder constructed its snapshot with all four flow inputs set to
zero:

```python
# minute_features.py:102-105
foreign_net_buy=0.0,
institution_net_buy=0.0,
program_net_buy=0.0,
retail_net_buy=0.0,
```

Fed those, the flow scorer returns exactly `50.0` for every input. I never wired up the flow data,
and nothing complained, because a constant is a perfectly valid score.

**Theme (0.20).** Every symbol was passed `theme="unknown"`, so the theme-strength computation had
exactly one group and returned an identical value across the whole cross-section. Two of its
internal terms were hardcoded to `50.0` besides.

**Lead-lag (0.05).**

```python
# scoring.py:46
lead_lag = 50.0
```

**Market regime (0.15).** Supplied by `demo_market_context()` — a function that returns fixed
synthetic values, written for the demo mode — on *every real-data path*: the replay runner, the
signal-quality dataset builder, the exit family search, the exit optimiser, and the live shadow
runner.

**Risk (0.05).** Credit-risk and disclosure-risk inputs defaulted to zero, pinning the inverse at
100.

Adding up: 0.15 + 0.20 + 0.15 + 0.05 = **0.55 of the composite was constant**, 0.60 counting risk.
What varied cross-sectionally was turnover, position relative to VWAP and the value area, and a
rule-based state machine. The eight-factor architecture was decoration over *relative turnover and
price above VWAP*.

The state machine, for its part, carried an honest label I had written and then stopped reading:

```python
# scoring.py:20
"""Rule-based placeholder for later HMM state probabilities."""
```

## The two selection bugs

Worse than the constants, because these produced numbers I believed.

**Threshold chosen on the test fold.**

```python
# signal_quality.py:142
best = max(threshold_reports, key=lambda row: (row["avg_net"], row["trades"]))
```

Five probability thresholds were each scored on the *test* fold and the maximum was recorded as
`"best"`. Base rates across four folds were **+0.31%, −0.11%, +0.08%, −0.21%**. The reported
"best" values were **+4.14%, +4.99%, +3.63%, +2.32%** on 43, 37, 17 and 27 trades. Turning
approximately zero into +4% by picking a filter threshold on the same data used to measure it is
the textbook failure, and it was four lines.

**A 420-cell grid ranked on out-of-sample.**

```python
# minute_optimizer.py:94-100
key=lambda item: (item.test_avg_exit, item.test_precision_mfe_3, item.test_avg_mfe)
```

Four hundred and twenty configurations, sorted by test performance, top one reported as best, no
multiplicity correction of any kind. `train_avg_exit` was computed on every row and never used for
selection.

Both violate a rule I had written down myself, in this repository's ancestor, months earlier:

> No threshold tuning on final out-of-sample data.

The protocol document was correct. The code did not read it.

## The claim that should have stopped me

```json
{ "filtered_base": { "mean": 2175.2, "p05": 1938.9, "loss_prob": 0.0, "mdd_median": -5.24 } }
```

A +2,175% mean, a 5th percentile of +1,939%, **zero probability of loss**, and a 5% median
drawdown. It is an unseeded bootstrap over the post-hoc-selected winning trades, so it inherits
100% of the selection bias and measures none of it.

The same file contains its own control, which is why it ships here — see
[postmortem 05](05-monte-carlo-selected-winner.md).

## Why the audit happened at all

Not from suspicion. I was deciding what from six weeks of work belonged in a portfolio, went
looking for the strongest artifacts in the scanner, and traced the score to its inputs. It takes
about ten minutes and it can be done by anyone reading the source. **Nobody had read the source,
including me, since writing it.**

That is the transferable part. The scanner's failures were not subtle and not statistical. They
were four hardcoded zeros, one hardcoded constant, a demo function on a production path, and two
`max()` calls on the wrong fold — all findable by reading, none findable by running more backtests
on top.

## What would have caught it earlier

- **A test asserting cross-sectional variance is non-zero for every sub-score.** Ten lines. It
  would have failed on day one for five of the eight.
- **Any test at all.** The scanner had zero. This repository's suite exists in direct response.
- **Auditing the measurement code before recording a conclusion, not after.** The habit that caught
  [two look-ahead bugs in the same session](02-self-caught-look-ahead.md) was not yet applied here.

## What would revive it

Nothing, and that is the honest answer. Wiring up the missing five sub-scores would produce a
different, untested strategy that happens to share a file. It would not rescue this one, because
this one was never the thing the architecture described. The correct action was to retire it and
keep the two artifacts that carry a finding.

## The tests that pin this

[`tests/test_audit_artifacts.py`](../../tests/test_audit_artifacts.py) — six assertions against the
scanner's own shipped output. They fail if either finding stops being true.

## What I take from it

Elaborate architecture is a place for errors to hide. Five constants survived inside an
eight-factor model for weeks precisely because the model was complicated enough that no single
number looked wrong. A simpler score — turnover and distance from VWAP, which is what it actually
computed — would have been visibly two features, and I would have known what I was testing.
