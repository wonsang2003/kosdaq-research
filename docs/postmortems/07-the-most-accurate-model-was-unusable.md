# Postmortem: the most accurate model I built, and why it was worthless

**Verdict:** REFUTED · **Cause of death:** fill fantasy · **What it cost:** the model was correct ·
**Reproduce it:** not from this repository — the orderbook panel it was fitted on cannot be
redistributed. What ships is the reasoning and the gate it produced

---

Twenty-seven features, gradient-boosted, walk-forward validated: **AUC 0.909** at predicting
whether a KOSDAQ name touching the intraday ramp would close locked at the daily limit.

That is the highest discrimination anything in this research achieved, and it is in the graveyard,
because on the subset a retail account can actually buy it returns **−3.00%**.

## The setup

The daily limit is +30%. A name that closes locked at the ceiling has a large buy queue and no
sellers; a name that runs to +20% and fades does not. Knowing which is which, in advance, is worth
a great deal — if you can act on it.

Validation was walk-forward and not k-fold, deliberately: with clustered market events, random
folds put the same panic morning on both sides of the split and the score is a memory test.
Training on rolling past windows and scoring the next one out of sample is the only split that
matches how the model would be used. AUC was stable across folds. There was no leakage I could
find, and I went looking twice.

## Where the discrimination lived

Then I asked the question that has to be asked of any classifier attached to an order: **not how
well it separates, but whether the separation sits anywhere I can trade.**

It did not. Almost all of the model's discriminative power came from the region where the price is
already at or adjacent to the ceiling. At that point the buy queue is enormous by construction and
a retail limit order joins the back of it. The model was, in effect, extremely good at recognising
a state that is definitionally unbuyable.

Restrict to the fills a small account can actually obtain and the return is **−3.00%** net. The
subset that is reachable and the subset that is predictable barely intersect, and where they do,
the sign is against you.

## Why AUC was the wrong number

AUC integrates over the whole score distribution. It answers *how well does this rank the
population* — which is the right question for a screening test and the wrong question for a
trading rule, because a trading rule does not act on the population. It acts on the subset where
the order fills.

Two conditions have to hold together, and the model was scored on the first alone:

$$
\text{tradable} \;\Longleftrightarrow\; \underbrace{P(\text{event}\mid x) \text{ is informative}}_{\text{the model}}
\;\wedge\; \underbrace{P(\text{fill}\mid x) > 0 \text{ at a price that keeps the edge}}_{\text{the market}}
$$

Nothing in the loss function knew about the second term. So the fitting procedure did exactly what
it was asked and walked straight into the region where the first term is maximised — which happens
to be the region where the second is zero. The model did not fail. It optimised an objective that
was missing a constraint.

The correct evaluation metric was available the whole time and I was not using it: **net return on
the fillable subset**, computed under the same execution assumptions as any other candidate. Under
that metric the model is negative and the verdict is immediate.

## The conditional-distribution trap underneath

There is a subtler failure sitting under the obvious one, and it is the reason this is filed as
*fill fantasy* rather than as a modelling error.

The distribution of outcomes conditional on *being filled* is not the distribution the model was
trained on. Orders fill when someone is willing to take the other side, and near a lock that
willingness is itself information — it means the queue is clearing, which is the ramp failing. So
the act of getting filled selects against the prediction.

This is measurable and it was measured, elsewhere in this research, by comparing orders that filled
against orders that did not: the unreachable set was several times better. A model fitted on all
events and deployed on filled events is fitted on the wrong population, and no amount of validation
on the training population detects it.

## What it produced

This is the incident that turned fill feasibility from a thing I checked afterwards into a gate
that runs first. Any candidate that transacts at a limit, an auction, or a thin book now has to
clear
[`fill_feasibility.py`](../../src/features/execution_realism/domain/services/fill_feasibility.py)
before its headline statistic is computed at all — because computing it first means spending the
effort and then arguing with yourself about a number you already like.

## What would change my mind

- **A fillable subset with positive net.** If the reachable region could be enlarged — a different
  venue, a different order type, a participant with queue priority — the model's discrimination is
  real and would transfer. The finding is about my execution, not about the market. See
  [institutional-delta](../institutional-delta.md).
- **Discrimination away from the lock.** If a re-fit restricted to states well below the ceiling
  retained useful AUC, the two subsets would intersect and this would be a different result. I did
  not find that; I did not search exhaustively for it either.

What would **not** change my mind: a better model. The constraint that killed this is not in the
hypothesis class.

## What I take from it

Model quality and strategy quality are different quantities, and the gap between them is entirely
execution. A classifier can be genuinely excellent and worth nothing, and the metric that tells you
so is not a model metric at all.

The uncomfortable part is that AUC 0.909 felt like the best result of the whole six weeks for about
a day. It was the most accurate thing I built, and being accurate was not the job.
