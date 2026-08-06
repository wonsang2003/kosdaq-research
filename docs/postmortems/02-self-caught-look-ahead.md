# Postmortem: two look-ahead bugs I found in my own measurement code

**Verdict:** conclusions retracted before publication · **Cause:** measurement error, not market
behaviour · **Caught by:** an adversarial audit of the measurement code run immediately before
writing results down permanently

---

## Why this one is here

The other postmortems describe strategies that failed. This one describes *analysis* that
failed — twice — and was caught both times by the same procedural habit: **audit the measurement
code adversarially in the window between reaching a conclusion and recording it.**

Both incidents produced clean, plausible, monotone results. Neither was real.

---

## Incident 1 — the early-entry level study

**The conclusion I reached:** moving the entry trigger earlier (from a +10% cross to +5–9%)
degrades predictive power monotonically. AUC fell 0.690 → 0.609 as the trigger moved earlier,
and a derived "speed rule" returned +2.23% at t = 2.12.

Monotone, mechanistically sensible, and with a tradable rule attached. I was about to write it
down.

**An adversarial audit of the measurement code found five defects, all in my code:**

1. **A silent fallback in a helper.** Data collection begins around a +5% premium, so for early
   levels the observation window is short. The helper `at(sec)` quietly returned the *start of
   observation* when the window was too short instead of raising. **Twelve of twenty-two features
   collapsed to constants at low levels.** The monotonicity was that collapse, not the market.
2. **An algebraic identity mistaken for a result.** The "+3.36%p paired comparison" was a
   logarithmic identity, because the exit price is constant within a stock-day.
3. **Instrument reproduction read as market fact.** Coverage rising 39% → 81% was the collector's
   own trigger being rediscovered — a stock that reaches limit-up must physically pass through 5%.
4. **Leave-one-out mistaken for walk-forward.** The speed rule used LOO, which includes future
   days. Under a true expanding window t ≤ 1.07 — **and the rejected group outperformed the
   group that passed.**
5. **A single `break`** recorded only the lowest level when several were crossed in one frame.

**Corrected finding:** 7–9% is *equivalent* to 10% (AUC@8 0.665 vs AUC@10 0.634); 5–6% is not
worse, it is **unmeasurable** with data whose collection is triggered above it. Unconditional net
day-clustered t is below 1.0 at every level.

Four conclusions were retracted before they were written down.

---

## Incident 2 — end-of-day context in a PIT feature

Extraction code read `turn` (turnover) and `mcap` from an end-of-day context snapshot while
labelling them as entry-time features. Turnover at the close is not knowable at 09:03.

Two things worth noting about the fix:

- **The corrected result was better than the contaminated one.** Look-ahead does not reliably
  flatter you; it just makes the number meaningless. Assuming a leak inflates results, and
  therefore that a good result is "safe", is itself an error.
- The same review found a second instance: a forward-scoring routine was **counting 11 backfilled
  sessions as forward observations**, which would have let a pre-registered rule be judged on the
  data that produced it.

---

## The confound that made it necessary

The reason this audit habit exists at all is that I did not believe my own result. I had written
down that exiting at 09:30 was worse, then went back to the raw orderbook because the conclusion
did not match the individual cases in front of me — plenty of them reached the ceiling on a 09:30
exit.

The analysis was wrong. `turn` and entry time are confounded: early-session
entries have a systematically different turnover distribution, so an entry-time cut and an
exit-time cut were being measured as one thing.

Decomposing them (paired, same entry set, exit rule varied alone) gives the clean answer:

| | |
|---|---|
| 09:30 exit vs close exit | **−0.764%p**, day-clustered t = **−1.80**, better on 2/12 sessions |
| Ceiling hit rate | 33% → **23%** |
| Where it hurts most | entry ≥12%: −1.06%p (t = −2.78) · high turnover: −1.44%p (t = −2.55) |

So the original direction was right and the reasoning was not, and the corrected version is
sharper: **cutting at 09:30 selectively amputates the winning trades.** A third of ceilings
arrive after 09:30, and the only subgroup it does not damage is the one that was losing anyway.

---

## What would change these conclusions

- **Incident 1:** collection triggered *below* the ranking entry point. The 5–6% question is
  unmeasurable with current data, not answered — that distinction is the finding.
- **The 09:30 exit:** a subgroup where the paired difference is positive with day-t > 2. Low
  turnover is the only candidate and it sits at +0.141%p, t = 0.29 — indistinguishable from zero,
  in a group that loses money anyway.

---

## The tests that pin this

- [`test_day_clustering.py::test_single_session_raises_rather_than_returning_zero`](../../tests/test_day_clustering.py)
  — a one-session result cannot enter a comparison table.
- [`test_day_clustering.py::test_drop_worst_session_removes_the_best_day`](../../tests/test_day_clustering.py)
  — the screening step that caught a candidate carried by one session.
- [`test_hypothesis_ledger.py::test_missing_ledger_raises_instead_of_returning_empty`](../../tests/test_hypothesis_ledger.py)
  — the silent-fallback shape from defect 1, in the form it takes here.

---

## What I take from it

Auditing the *measurement code* is a different activity from auditing the *result*, and the
result can be internally consistent, monotone, and mechanistically plausible while the code that
produced it is broken in five places.

The procedural rule that came out of it: **before recording a conclusion permanently, adversarially
audit the code that produced it, on the assumption that it is wrong.** Both times, it was.

A related and equally important habit: **measure what fraction of random rules a test passes.**
An orderbook harness was found passing arbitrary filters 64–86% of the time and passing random
rules through six-day leave-one-day-out at 18.5%. Every "significant" result that test had ever
produced was void. If the random pass rate materially exceeds 5%, discard the conclusions, not
just the candidate.
