# Survivors

Three of roughly ninety-eight strategies cleared every gate. This directory describes them at the
level of mechanism and evidence, not specification.

| | What it is | Status |
|---|---|---|
| **[S1](01-event-driven-long.md)** | Corporate-action event, long | Live, own capital |
| **S2** | A model-based gate over an event family. Walk-forward positive in 8 of 8 years; the gate is pure cross-sectional selection, verified by an ablation that removes same-day event count and leaves the result unchanged | Live, own capital |
| **S3** | A second event family, with a strictly point-in-time feature construction. Walk-forward positive in 8 of 8 years | Live, own capital |

S2 and S3 have no separate page. Publishing three spec sheets from one research programme would
triangulate it even with every individual field withheld — the *shape* of a redaction
leaks, and three near-identical blanks are more informative than one.

## Live, but not confirmed

Two further books are running forward under pre-registered judgement. Neither has cleared a
confirmation bar, neither is counted among the three above, and both are described here because
leaving them out would make the record look tidier than it is.

**A prediction-market book.** The only book here outside Korean equities, and the only one whose
edge is a *pricing* bias rather than a forecast: a double-calibration test found the raw market
price beating every model fitted to the underlying forecast variables — Brier **0.0462** against
**0.0495–0.0516** — so whatever edge exists is in the price structure, not in predicting the event
better than the market does. That result is what killed the modelling arm and is recorded in
[GRAVEYARD.md](../../GRAVEYARD.md) under class 10. A pre-registered n ≥ 60 judgement is in progress.
During the run a realised-loss decomposition forced a size cut — a live parameter change made
against a rule written before the losses, not after them. Bands, size ladder and governor constants
are withheld.

**A multi-day KRX book.** Live core line, described at class level only: mechanism family,
day-clustered significance and the sign of each calendar year are the published surface, and the
rule is not. Its most useful output so far is not a return — it is a defect, and that one is
published in full at
[postmortems/06](../postmortems/06-three-audits-of-my-own-plumbing.md): the original backtest
double-counted concurrent entries because production carries a concurrency constraint the backtest
did not model. **The backtest was more permissive than production.** That is the mirror image of the
usual failure, and of the other defect in the same postmortem.

Neither book changes the count in [GRAVEYARD.md](../../GRAVEYARD.md): forward-only means undecided,
and undecided is not a survivor.

## What is published, and what is not

Withheld across all three: signal definitions, entry and exit rules, holding periods, filter sets,
feature names, model artifacts, event counts, per-trade magnitudes and year-by-year levels.

Published across all three: mechanism at class level, the execution property that makes them
fillable, day-clustered significance, the sign of each calendar year, the falsification checks each
one had to pass, the wounds found in each, and the pre-committed conditions under which each would
be abandoned.

The reasoning is in [DISCLOSURE.md](../DISCLOSURE.md). Short version: you cannot protect an idea,
only the validation — so what is withheld is the material that would confirm a guess, not the
material that would prompt one.

## An honest note on the asymmetry

The graveyard runs to several thousand words of forensic detail; this directory runs to one page.
That is not an accident of effort. **The graveyard's detail is what pays for this page's silence** —
a reader who has watched ninety-five strategies dissected, including three autopsies of my own
code, has enough evidence about the *process* that the survivors do not have to carry the argument.

If the graveyard were thin, this page would need to be thick, and then there would be nothing left
to protect.

## A component, not a strategy

One further result cleared every gate but cannot open a position: an exit rule for fast-trending
positions, which beat holding to the close across every year tested. It is a component
available to transplant into other strategies and is worth nothing on its own — an exit with no
entry earns exactly zero.

It is withheld at the same tier as the rest, for a specific reason: the rule is short enough to
state once and reuse. The competence it would demonstrate is already demonstrated publicly
elsewhere in this repository, attached to a **dead** strategy — see the limit-up entry arithmetic in
[GRAVEYARD.md](../../GRAVEYARD.md) and
[`test_market_arithmetic.py`](../../tests/test_market_arithmetic.py), where the same reasoning is
worked out at full precision on a corpse.

That substitution is the general pattern here: **prove the skill on a corpse.** There are
ninety-five of them.
