# Graveyard

A record of what was tested and what killed it, grouped by cause of death.

This document has a working use, not a documentary one. When a new idea arrives it is matched
against these classes first, and **a match is grounds not to run the backtest at all.** The
seven original classes were frozen in the discovery log after ~40 kills; four were added later,
each because something died in a way none of the seven described.

Scope: **~98 strategies across four markets** — KRX equities (~55), Korean crypto arbitrage (~22),
Korea-stock perpetuals (7), prediction markets (~14). Three survived.

**What is published here, and what is not.** Every entry names the strategy and the cause of
death. The measurement that established each cause — the statistic, the counterexample, the
diagnostic that separated a real effect from an artifact — is **withheld**, on the same policy the
survivor sheets use. A cause of death is a conclusion; the measurement that produced it is a method
I paid for in time, and several of them required real money in real order books to obtain. They are
available under NDA or in an interview. Four are worked in full in
[docs/postmortems/](docs/postmortems/), which is where the depth is demonstrated rather than
distributed. See [DISCLOSURE.md](docs/DISCLOSURE.md).

---

## 1. Fill fantasy

*The backtest transacted at a price that cannot be obtained.* The most common and most
expensive class. Predictive power tends to live exactly where the queue does not clear.

Diagnostic → [the fill-feasibility predicate](MODELS.md#auction-allocation-and-the-fill-feasibility-predicate)
and [the net-to-ceiling identity](MODELS.md#the-net-to-ceiling-identity--determinism-as-a-refutation).

| Strategy | Cause of death |
|---|---|
| **LOCK** — limit-up close → next open | No allocation in the closing auction |
| **2-consecutive-green overnight** | The surviving signal set was concentrated in unbuyable closes |
| **GD** — gap-down opening bounce | Live orders did not fill, at a rate the model itself predicts |
| **soft-lock overnight** | The edge was entirely in the pinned, unbuyable subset |
| **fillable limit orders at the ramp** | Getting filled cheaply *is* the failure event |
| **STALE** — overnight signal → pre-market | Pre-market fillable volume was never measured, and is thin |
| **SSF gap-up fade** | Signal confirms at the open; entry at that price is a ghost leg |
| **P(limit-up), 27 factors** | The most accurate model built here. All of its discrimination sits in the region already locked |
| **new-listing premium ladder** (crypto) | Small-sample and favourable-exit bias; reversed on full history |

**One of these has a successor.** The gap-down family was re-designed after its death — different
entry mechanism, different exit — and two cells of the redesign cleared a pre-registered gate. It
runs in shadow, unfunded, and it is recorded here rather than among the survivors because a
pre-registered gate passed once is not a confirmation. The cell definitions are withheld for a
mechanical reason: they encode their own thresholds, so naming a cell publishes the rule.

**Rule extracted:** every backtest that transacts at a limit-up, an auction, or a thin book must
carry an explicit fill-feasibility check before its t-statistic is reported.

---

## 2. Cost floor

*The gross edge is real and statistically solid, and smaller than the friction it must cross.*
In Korea the binding term is almost always the sell-side transaction tax.

Diagnostic → [the cost floor, and why break-even is endogenous](MODELS.md#the-cost-floor-and-why-break-even-is-endogenous).

| Strategy | Cause of death |
|---|---|
| **overnight cross-sectional excess** | Market-neutral and highly significant, and below the transaction tax. Also decaying |
| **opening volume-direction fade** | Every cell of the grid net-negative. Strongest in large caps, refuting its own retail story |
| **perp → KRX arbitrage** | Even the most predictable decile is a fraction of the round trip |
| **dividend capture** | The naive number is the tax calendar; the pure ex-day effect is negative after withholding |
| **9 crypto microstructure families** | Genuine predictive power, over a distance smaller than the spread |
| **USDT/USDC-KRW reversion** | Reversion is real; its amplitude is a fraction of the spread |
| **cluster cross-venue micro-arb** | Survives every statistical gate, then inverts under real bid/ask crossing. A spread problem, not a speed problem |
| **index gap → long / inverse ETF, 35 cells** | The tick grid alone consumes the gross. Every inverse cell negative |
| **sub-limit momentum overnight** | Decays into the friction floor across the sample halves |

**Rule extracted:** compute the cost floor *before* the significance test. If gross < friction,
the significance test is a waste of compute regardless of how it comes out.

---

## 3. Survivorship manufacture

*The edge was the absence of delisted names, not the signal.*

| Strategy | Cause of death |
|---|---|
| **smallcap bounce** | The largest statistic of the search was its largest artifact |
| **gap-down bounce** | Sign undetermined once delisted names are restored |
| **M&A event-driven long** | Collection filtered on currently-listed issuers, so even the negative results are an upper bound |
| **event-driven long** *(control)* | Survived the same recompute nearly unchanged. Reported as the control that makes the others readable |
| **cross-sectional momentum** | Fails against its own no-factor benchmark |

Korean delisting has a distinct price signature, and a walk-forward filter on a handful of
observables removes most traps. The variables and thresholds are withheld: it is a reusable
universe-hygiene tool built on a panel that is hard to assemble.

The filter is worth nothing as alpha, which is the part worth publishing: among surviving names it
is indistinguishable from a random subsample. **It is mine-clearing, not edge** — and conflating
the two is how a risk control gets sold as a strategy.

**A hard structural limit:** the flow panel and the delisted panel do not join, so small-cap flow
strategies cannot in principle be survivorship-tested with available data.

---

## 4. Multiplicity *(extension)*

*The reported figure is the maximum of a search, not an estimate.*

Diagnostic → [the noise ceiling √(2 ln k)](MODELS.md#the-noise-ceiling--sqrt2-ln-k) and
[session-clustered t](MODELS.md#session-clustered-t).

![Per-event vs session-clustered t](docs/figures/per_event_vs_clustered_t.svg)

| Search | Cause of death |
|---|---|
| 13-filter argmax | Family-wise correction leaves nothing; combination stability collapses under leave-one-out |
| gap-down cell matrix | The winner sits inside its own best-of-k null |
| 300-combination retail-pattern census | Best result is unremarkable against the best-of-k null |
| the "diagonal" method | Mean effect negative; the count above the bar is exactly chance |
| crypto price-only daily | Nothing survived out of sample; a random-signal null matched the real families |
| BTC variance-risk-premium | Below the deflated-Sharpe noise floor at that trial count |
| convertible-bond event, **second life** | Killed, resurrected, killed again on a grid frozen before the re-run. A data bug surfaced during the recount and was fixed before the verdict |
| 92-hypothesis search | Survivors landed on the noise ceiling for their own grid widths. Nothing in the pass/fail logic objected |

**Rule extracted:** log the cell count with every result and compare against √(2 ln k) as well as
the nominal bar. The nominal bar passes candidates that are pure noise maxima.

---

## 5. Regime concentration

*Performance confined to one market state, reading as general because the sample is dominated
by it.*

| Strategy | Cause of death |
|---|---|
| **L3ETF** | Born in one regime and absent before it. **Corrected:** re-opened after this finding and survived a block-bootstrapped recheck; now forward with armed kill lines, and the regime concentration stands as its stated failure mode rather than its cause of death |
| **cross-sectional gap fade** | Positive only in the retail-boom and short-resumption years |
| **gap-down bounce** | The overwhelming majority of profit on a single panic day. The real identity was "market-panic dip buy" |
| **K200 overnight** | Concentrated in the most recent stretch of the sample |
| **DWIN** | Concentrated in a handful of days |
| **SCWK** | Full-sample significance does not survive into the out-of-sample half |
| **Polymarket fade premium** | Premium compressed to below cost, then flipped |

**Rule extracted:** drop-top-day belongs at *screening* time, not as a post-hoc excuse. Nearly
every candidate that reached late-stage review collapsed on it.

---

## 6. Unobservable at decision *(extension)*

*The conditioning variable is not knowable when the order must be placed.* Distinct from
look-ahead in that no code is wrong — the information does not exist yet.

- **GD gap-down bounce.** The edge conditions on a quantity that is only determined after the
  order deadline. The pre-deadline estimate carries almost no information about it.
- **The paper track was a look-ahead replica.** Cells were selected and entered on a price
  unknowable before the open; the live and paper symbol sets did not overlap at all.
- **HOGA early entry.** The cheap-entry window and the predictable window do not overlap.
  *Early is cheap but blind; late is predictable but expensive; there is no window that is both.*
  **Status:** the chase form is refuted by arithmetic and stays refuted. A differently assembled
  form went into a pre-registered 30-session forward judgement with the model frozen beforehand and
  is early in that window — pending, not closed. Its components are withheld: the names encode
  their own thresholds.
- **Korea-stock perp opening convergence.** The auction already clears at the gapped price, so the
  perpetual's directional skill has nothing left to harvest.

---

## 7. Instrument absent

*The edge is real and reachable in principle, and no tradable instrument exposes it to this
participant.*

| Edge | Why it cannot be taken |
|---|---|
| **KOSDAQ composite short** | No future is written on the composite, and most of the alpha sits outside the tradable subindex |
| **gap-up fade short** | Live lending inventory on this board is effectively zero |
| **MN — market-neutral hedged construction** | Requires several times the available capital, and died a second time on intraday execution decay |
| **M&A short drift** | Same retail short wall |
| **ProBit integration** | Venue permanently terminated |

**The five structural locks:** no instrument · short impossible · the sell-side tax · speed ·
capacity. Each was measured rather than assumed; the measurements are withheld and the classes are
the transferable part.

**Consequence:** every survivor sits on the opposite side of retail chase-buying. Not one is a
chase.

---

## 8. Beta disguise

*Market or style exposure reported as alpha.*

Diagnostic → [beta separation](MODELS.md#beta-separation).

- **low-volatility long** — defensive beta, not skill
- **disclosure drift** — "market-adjusted" meant subtracting one index at an assumed β of 1. Under
  a proper market model the same event window changes **sign**. The first KRX event study here to
  do so, and the reason this section is a gate rather than a caveat
- **long baskets without an index gate** — the gate was doing the work
- **crypto funding carry** — passed every statistical gate out of sample, and a random always-on
  null nearly matches it. High Sharpe was diversification, not timing
- **cross-venue funding dislocation** — being paid to warehouse counterparty risk, not alpha

The same arithmetic runs in reverse and **rescued** a strategy: a raw loss inside a much larger
market decline is a positive beta-adjusted excess, and the sign is robust to any plausible beta.
See [`beta_separation.py`](src/features/falsification/domain/services/beta_separation.py).

---

## 9. Adverse selection *(extension)*

*Fills are obtainable; the subset that fills is the subset that loses.*

- **HOGA size ladder** — the orders that could not be filled were substantially better than the
  ones that could, and the gap widens with size. The size-by-size curve is withheld: it is a
  market-impact dataset obtained by putting real money into real books
- **ceiling-precursor** — fill rate collapses exactly where the signal fires, and slippage
  finishes it
- **cross-border jump capture** — no pair cleared an honest-fill sweep. The apparent premium is
  quote staleness, universal rather than venue-specific
- **pure market making** — positive short-horizon markout, negative realised round trip. Inventory
  run-over during trends exceeds the bounce
- **Polymarket momentum arms** — crossing entries, pooled return negative with the interval
  excluding zero

---

## 10. Nothing there

No effect survived first contact. Recorded so they are not silently re-tested.

**KRX:** per-stock elasticity constants · flow persistence · PEAD · day-of-week · tail ratios ·
MA20 breakout-gap long · lower-shadow bounce · quiet accumulation · NR compression→expansion ·
full gap recovery · run length · cross-market 5-minute rules · pair-trading and lead-lag broadly ·
parent-subsidiary pairs · new-high close-auction buying · RSI band reversal and divergence ·
pullback-to-support reversal · first-five-minute surge capture · a unified 20-type disclosure event
model · attention · close markdown reversal · forced-liquidation D+2 · expiry-day · iNAV proxy ·
sector/theme ETF overnight · inverse ETF buying

**Crypto:** intra-Korea lead-lag (both venues are downstream of the same offshore book) ·
triangular arbitrage · funding extremes · slow-horizon gap reversion · cross-sectional
momentum/reversal · small-venue inefficiency (the venues are genuinely not price-efficient, and the
gap is always smaller than their own spread)

**Perpetuals:** millisecond lead-lag (the apparent lead was argmax noise on a flat curve) ·
cross-venue basis (an index reference offset, not a basis)

**Prediction markets:** ML over forecast variables (the raw market price beats every fitted model) ·
intraday entry · volatile-regime expansion · cold-side fade (high win rate, negative return) ·
overshoot conditioners · BTC 5-minute direction

---

## 11. Deployable-universe restriction *(extension)*

*Every statistical gate passes, and the alpha turns out to live entirely in the names the
deployment universe excludes.* The mirror image of class 3: there the delisted names were the
artifact, here **including** them made the result look better.

- **KOSPI gap-down, model-gated promotion.** Strongly significant on the full panel, and adding
  the delisted panel *improved* it, which rules out survivorship in the ordinary direction.
  Restricted to the universe actually deployable — ordinary shares only, a minimum traded value, no
  stale prints — it does not clear the bar, and drop-top-3 finishes it. The significance was living
  in preferred lines and in names whose last print is hours old.

**Rule extracted:** the deployable-universe restriction is the **last** gate, applied after every
statistical gate has passed rather than instead of them. Applied earlier it saves compute; never
applied, it is how a large t gets promoted into a universe that cannot produce it.

---

## Cancelled before deployment

*Not kills. Projects stopped by a gate that fired before any capital, and in one case before any
strategy code, existed.* They are recorded because a pre-registration that never cancels anything
is decoration.

- **macro-gap overnight** — a canary condition written into the pre-registration fired during
  design review. Cancelled at **zero lines of strategy code**. The only time that clause has fired,
  and the reason it is still in the template.
- **social-post-count regime model** — the declared start condition was never met. Design frozen
  and shelved rather than started on a shorter sample.
- **KBO win-probability model** — discarded, and it left a standing rule behind: **no trading on a
  probability model before its calibration has been tested.** The prediction-market families later
  confirmed exactly that failure.
- **eight-factor KR long/short book** — designed, never run. The short leg needs borrow that does
  not exist for this participant (class 7), so it degrades into a different strategy from the one
  that was designed.

---

## What this leaves

| Verdict | Count |
|---|---|
| **CONFIRMED** | **3** — one event class, described at [docs/survivors/README.md](docs/survivors/README.md) |
| Confirmed as a *component* | 1 — an exit rule. It cannot open a position, so on its own it earns zero |
| Confirmed as *avoidance* (not position-generating) | ~10 defensive rules |
| UNDECIDED, forward-only | 7 — running under pre-registered judgement. Two at [docs/survivors/README.md](docs/survivors/README.md#live-but-not-confirmed) |
| Cancelled before deployment | 4 |
| **REFUTED / RETIRED** | **~85** |

The three survivors share exactly one property none of the other ninety-five had:
**the price the backtest assumed is, by definition, a price you can actually get.**

---

*If a new idea falls into one of these classes, do not re-test it.*
