# Disclosure policy

What appears in this repository, what does not, and why. Applied uniformly — the rules below were
written before the documents and every page was checked against them.

A repository that publishes a live edge is telling a prospective employer that their edge would not
be safe either. So the policy is the same one I would apply to work done at a firm: **publish the
method and the failures in full; publish enough of the survivors to let the work be judged; publish
nothing that lets them be rebuilt.**

---

## The governing distinction

You cannot protect an idea. You can only protect the **validation**.

A reader who correctly guesses the mechanism behind a live strategy still faces the six weeks of
work that separates a guess from a decision. A reader handed the trigger, the filter set, the
holding period and the sample size faces an afternoon. So the redaction target is not the
*suggestive* material — it is the *confirmatory* material: anything that says **"yes, you guessed
right, and here is the version that works."**

**Corollary: an identifier can be a disclosure.** Several live cells and features in the original
research are named after their own thresholds — the label carries the level. Pasting a cell name
into a document therefore publishes the rule it encodes, whether or not the surrounding sentence
mentions a number. Everything here uses neutral labels for that reason.

**Corollary: a blocklist is a disclosure.** A test asserting that a value must not appear publishes
that value, and this repository learned it the hard way — the guard in
[`test_figures.py`](../tests/test_figures.py) once held the withheld numbers and the live mechanism
name as regex literals. Hashing does not save it either: these tokens are low-entropy, and a
four-digit number is ten thousand guesses. The list therefore lives **outside** the repository, the
guard reads it from a path the tree never contains, and the two shape-level patterns that remain
inline describe a *form* rather than any real name.

**Corollary: anything indexed by date is disclosure.** Trading dates join to public exchange and
filing data. An equity curve with a calendar axis, a test fixture carrying real index levels for a
real week, or a forward reading reported at a specific sample size all narrow down positions that
were actually held. This repository contains no calendar axis on any live-strategy figure, and its
test fixtures use synthetic levels on fictional dates.

---

## Five tiers

| Tier | Rule |
|---|---|
| **T0 — publish freely** | Public market structure · the method · every failure · the engineering |
| **T1 — publish at class level** | Mechanism family, universe, instrument class — coarse enough not to narrow the search materially |
| **T2 — publish decoupled evidence** | Statistics that establish rigour without localising the rule |
| **T3 — withhold, acknowledge** | Thresholds, feature names, filter lists, entry and exit timing, holding period. Publish *that it was tested*, never the value |
| **T4 — never publish** | Executable specifications, model artifacts, real-row fixtures, trade-level or date-indexed data |

Applied per attribute:

| | Live / confirmed | Forward-only | Real but unreachable | Refuted |
|---|---|---|---|---|
| Mechanism · universe | class only | class | class + why blocked | **full** |
| Signal names · thresholds · filters | never | withhold | withhold | full |
| Entry timing · exit rule · holding period | withhold | withhold | withhold | full |
| Feature list | count and families, no names | " | " | full |
| **t-statistic** | **publish** | publish | **publish** | full |
| Year-by-year record | **signs** | signs | full | full |
| Trade-level or date-indexed data | never | never | never | dates dropped |
| Cost model · protocol · **failure modes** | **publish maximally** | " | " | " |

**Why the t-statistic is published and the sample size is treated more carefully.** A t-statistic
is not invertible to a rule and it is the core unit of credibility, so withholding it would cost
everything and protect nothing. Event frequency is different: it is a fingerprint. Counting filings
per year by type against a public disclosure system and matching a published frequency identifies
an event family without it ever being named. Where a count would function that way, it is reported
as a ratio or a band instead.

Similarly, a mean published together with a t-statistic and a sample size gives up the standard
deviation, and therefore the whole return distribution. Any two of the three, not all three.

---

## What "unreachable" means, and why those are published in full

A large part of this repository is strategies that are **real, statistically solid, and unavailable
to me**: blocked by a transaction tax, by borrow inventory that does not exist, by an instrument
that was never listed, or by a capital requirement several times my account.

Those are published with their statistics and their blocking analysis, because they are dead from
where I stand and because *why* they are dead is the most useful thing I know. What is withheld
even there is the executable pairing — the specific instruments, the hedge ratio, the trigger
level. That half is roughly none of the hiring signal and roughly all of the reconstruction value:
*"a market-neutral construction in large caps at a Sharpe near 2.7 that I could not afford"* is
exactly as informative about the research as naming the legs, and materially less useful to a
reader who wants to run it.

See [institutional-delta.md](institutional-delta.md).

---

## Two exceptions inside the graveyard

Almost everything about a refuted strategy is safe to publish, and the graveyard is deliberately
forensic — **its detail is what pays for the survivors' silence.** Two items are held back anyway,
because they are reusable infrastructure rather than dead ends:

1. **The delisting price signature and the filter built on it.** A walk-forward screen on four
   observables removes about three quarters of delisting traps. That is a universe-hygiene tool
   with standing value to anyone running Korean small caps, and it rests on a panel that is hard to
   assemble. The finding is published — *survivorship is worth roughly 60% of a small-cap edge
   here* — and the variable set and thresholds are not.
2. **The fill-rate-versus-order-size ladder.** A market-impact dataset obtained by putting real
   money into real books. The headline ratio is published, because it is the part that carries the
   lesson; the size-by-size curve is not.

---

## What is deliberately *not* redacted

- **Every failure, at full precision**, including the ones in my own code. Three of the five
  postmortems are autopsies of work I did and then found wrong.
- **The method, entirely.** The falsification battery, the multiplicity correction, the
  day-clustering, the beta separation, the fill-feasibility check. Method is not alpha.
- **The wounds in the surviving strategies** — the capacity error, the horizon at which forward
  testing stops being able to confirm anything. Generous disclosure of what is wrong with a
  strategy is what earns silence about what is right with it.

---

## Availability

Any withheld specification can be walked through under NDA, or in an interview. The point of this
document is not that the information is unavailable — it is that it is not published on the open
internet, which is a different decision and the one I would expect an employer to make too.
