# Postmortem: three audits of my own plumbing, and what each one invalidated

**Verdict:** no strategy died here — *records* died · **Cause of death:** engineering, not statistics ·
**Reproduce it:** not from this repository. The artifacts are live journals and production logs from
the running system. What ships is the principle, and the places it is enforced in this code

---

Every other postmortem here is about a strategy that was wrong. These three are about the machinery
that was supposed to tell me whether a strategy was wrong, and they are the ones I would want a
reviewer to read, because a statistical error costs you a hypothesis and a plumbing error costs you
the ability to know anything at all.

## Audit 1 — the silent skip

I went looking for every place the live system could decide not to act and not say so. The count was
**162 skip paths with no log line**, of which **44 were trade-critical** — a position not opened, an
exit not taken, a job not run.

Enumeration is not the finding. The finding is what sat inside them: **8 confirmed cases, and 4 more
on a second pass, where a data failure was indistinguishable from a normal skip.** A quote lookup
that failed returned the same thing as a quote lookup that legitimately found no candidate. On a
crash day, when the upstream throttles and lookups fail together, that ceases to be a logging
nicety: the system reports a quiet day and the journal agrees with it.

The rule that came out of it:

> **"lookup failed" and "not applicable" must not share a code path.**

They are opposite facts about the world and they were being encoded as the same return value. The
repair was a nine-class regression suite in the live system — one class per distinct way a skip can
be reached — so that each reason is asserted separately rather than inferred from silence.

**Where this repository enforces the same rule.** Two places, both checkable:

- [`test_equivalence_with_production.py`](../../tests/test_equivalence_with_production.py) ends with
  `test_production_file_is_reachable_in_this_environment`, whose entire job is to fail when the other
  tests in the file would otherwise skip. A suite that loses coverage should go red, not green.
- [`day_clustering.py`](../../src/features/falsification/domain/services/day_clustering.py) **raises**
  on a single session rather than returning a number. There is no t-statistic for one session; the
  alternative to raising is returning something that looks like an answer.

## Audit 2 — the ledger

I rebuilt the trade ledger from the written specification instead of from the journal, and compared.

**One strategy's record did not survive the rebuild at all** — the reconstruction and the journal
disagreed on which trades existed, so the recorded performance was not a measurement of the rule.
It was withdrawn rather than adjusted.

**A second strategy changed sign: +2.12% became −0.35%.** The cause is worth stating precisely,
because it is invisible in aggregate and obvious once named. A delayed-exit job meant some positions
were sold later than the rule specified. Which ones? Not a random subset — the ones that were still
open, which on a delayed-exit path skews toward **the ones that had kept going up**. The delay was
not noise added to the result; it was a selection rule that admitted winners and excluded losers,
and it inflated the mean by more than the entire reported edge.

This is the same defect as a fill-fantasy backtest, arriving from the opposite direction: there the
backtest assumes a price the market will not give, here the ledger records a price the *rule* would
not have taken.

## Audit 3 — the backtest was more permissive than production

The third one is the mirror of a defect found earlier in the same codebase.

The earlier one, the ordinary kind: **production had drifted from the written specification.** The
code was doing something the spec did not say.

The later one: **the backtest was doing something production could not.** It double-counted
concurrent entries, because the live system carries a concurrency constraint — a cap on how many
positions may be open at once — that the backtest did not model. The backtest was therefore trading a
strategy that the deployed system is structurally incapable of trading, and every number it produced
was for that other strategy.

Both directions matter, and only one of them is usually looked for. "Does production match the spec"
is a question people ask. "Does the backtest obey the constraints production actually has" is the
same question with the terms swapped, and it is the one that flatters you if you skip it.

## What I take from all three

A strategy result is a claim about two things at once — the market, and the machine that measured it.
Almost all of the discipline in this repository points at the first. These audits are the case for
pointing an equal amount at the second, because **the failure mode is not a wrong number, it is a
number that no longer refers to anything.**

None of the three was found by a test failing. All three were found by going to look. That is the
uncomfortable part, and it is why the enumeration in Audit 1 — 162 paths, counted by hand — is
recorded here as the method rather than as an anecdote.

## What would change my mind

- **A skip class that cannot be separated in principle.** If some failure is genuinely
  indistinguishable from a legitimate no-op at the point of decision, the rule above is too strong
  and the honest response is an explicit "unknown" state rather than either branch.
- **The delayed-exit inflation reversing under a longer sample.** The mechanism argues it cannot —
  it is a selection effect, not a variance effect — but the mechanism is an argument and the sample
  is the evidence.

What would **not** change my mind: the two invalidated records happening to be reconstructable later
from another source. A record that needs to be rescued was never a record.
