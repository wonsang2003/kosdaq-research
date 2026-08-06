# Models

Every estimator used to price, judge, or kill a strategy in this repository — with its
definition, its assumptions, and the incident that forced it into the protocol.

**Nothing here is decorative.** Each entry is attached to a specific kill or a specific
survivor. There is no Black–Scholes section, because nothing in this research priced a
derivative; the pricing work is microstructure-constrained and is described as such in
[§2](#2-pricing-under-microstructure-constraints).

Every entry carries a status:

| Status | Meaning |
|---|---|
| **implemented here** | Code and tests ship in this repository. The formula, the function, and the assertion that pins it are all linked. |
| **quoted from the research record** | Computed in the original research, reproduced here as a result. The estimator is defined so the number can be read, and the absence of an implementation is stated rather than implied away. |

The second tag exists because claiming an implementation that is not present would be
precisely the category of misleading claim this repository documents.

## Notation

| Symbol | Meaning |
|---|---|
| $x_i$ | one event's net return, in percent |
| $\bar x_s$ | mean net return within session $s$ |
| $S$ | number of trading sessions — **the effective sample size** |
| $n$ | number of events or trades |
| $k$ | number of specifications a search ranged over |
| $\alpha$ | nominal significance level, $0.05$ throughout |
| $\Phi, \Phi^{-1}$ | standard normal CDF and its inverse |
| $r$, $r_m$ | strategy net return and market return over the same holding period |
| $\beta$ | exposure of the strategy to the market |
| $e$ | entry premium over previous close, in percent |
| $c$ | ceiling premium, $\approx 29.5\%$ after tick-grid flooring |
| $f$ | round-trip friction, fixed at $0.38\%$ |
| $\delta(P)$ | KRX tick size at price $P$ |

---

# 1. Significance under dependence and selection

The section that did most of the killing. Every strategy in this repository produced a
statistic; the question was never whether the statistic was large but whether the
procedure that produced it could have produced it from noise.

## Session-clustered t

**Status:** implemented here · **Guards against:** event clustering

**Definition.** Events inside one trading session are not independent. A single panic
morning produced 62 signals; one cell held 1,220 events spread over 304 sessions. The
unit of independence is the session, so the effective sample size is $S$, not $n$.

**Assumptions.** Session means are approximately i.i.d. across sessions; $S \ge 2$;
sessions are weighted equally, so the reported mean is the mean of session means and
not the mean of events. The equal weighting is the point — event weighting is what lets
one crowded day dominate.

**Estimator.** Group events by session, average within, then test the session means:

$$
\bar m \;=\; \frac{1}{S}\sum_{s=1}^{S}\bar x_s,
\qquad
t \;=\; \frac{\bar m}{\hat\sigma_m/\sqrt{S}},
\qquad
\hat\sigma_m^2 \;=\; \frac{1}{S-1}\sum_{s=1}^{S}\bigl(\bar x_s-\bar m\bigr)^2 .
$$

The $\sqrt{S}$ in the denominator rather than $\sqrt{n}$ is the whole correction. When
events cluster, $n \gg S$ and the naive standard error is too small by roughly
$\sqrt{n/S}$ — for the cell above, $\sqrt{1220/304} \approx 2.0$, so a per-event $t$ is
inflated about twofold before any other consideration.

**Why this estimator.** Three recorded reversals, all in the same direction:

| Per-event | Session-clustered |
|---|---|
| $t = 12.7$ | $t = -0.92$ |
| $t = 7.4$ | $t = 0.4$ |
| "2026 decay, negative" | $+2.09$, positive |

The third is the uncomfortable one: the naive statistic produced a false *negative*, and
a verification script reported decay that was not there.

**Refusal, not zero.** Handed a single session the function raises rather than returning
$t = 0$ or `None`. One session is one observation; a number returned there would sit in
a comparison table beside a twelve-session figure as though the two met the same
standard.

**Implementation.** [`day_clustering.py`](src/features/falsification/domain/services/day_clustering.py) ·
carrier entity [`clustered_stat.py`](src/features/falsification/domain/entities/clustered_stat.py), whose
`n_sessions` field *is* the effective sample size

**Test.** [`test_day_clustering.py`](tests/test_day_clustering.py) —
`test_clustering_collapses_a_single_dominant_session`,
`test_single_session_raises_rather_than_returning_zero`

**Result it produced.** `day-clustered t = -3.43` in `make run`; the survivor's
significance in [survivors/01](docs/survivors/01-event-driven-long.md); every t in
[GRAVEYARD.md](GRAVEYARD.md).

## Drop-best-session influence check

**Status:** implemented here · **Guards against:** regime concentration

**Definition.** A leave-one-out influence diagnostic at the session level: remove the
single best-performing session and recompute. Applied at *screening* time, which is what
separates it from a post-hoc excuse.

**Estimator.** With $s^{\ast} = \arg\max_s \bar x_s$, recompute $t$ over
$\{1,\dots,S\}\setminus\{s^{\ast}\}$.

**Why this estimator.** Concentration was endemic: one strategy had 85% of its profit on
a single day, another 74.5% in its top twenty. A candidate carried by one lucky session
is indistinguishable from a real edge on the headline number and obvious under this one.

**Implementation.** [`day_clustering.py`](src/features/falsification/domain/services/day_clustering.py) ·
**Test.** [`test_day_clustering.py`](tests/test_day_clustering.py)

**Result it produced.** In `make run` the shipped refuted strategy goes from
$t = -3.43$ to $t = -3.87$ once its best day is removed — the loss is *more* robust
without it, which is the direction that supports the verdict.

## Bonferroni family-wise bar

**Status:** implemented here · **Guards against:** multiplicity

**Definition.** Testing $k$ hypotheses at level $\alpha$ each inflates the probability of
at least one false rejection. Bonferroni controls the family-wise error rate by
tightening each individual test.

**Assumptions.** Valid under arbitrary dependence between tests — conservative by
construction, which is the property wanted when the dependence structure of a search is
unknown.

**Derivation.** By Boole's inequality, for null hypotheses $H_1,\dots,H_k$,

$$
\Pr\Bigl(\bigcup_{i=1}^{k}\{\text{reject } H_i\}\Bigr) \;\le\; \sum_{i=1}^{k}\Pr(\text{reject } H_i) \;=\; k\alpha' .
$$

Setting $k\alpha' = \alpha$ gives $\alpha' = \alpha/k$, and inverting the two-sided
normal quantile gives the required threshold:

$$
t_{\text{req}}(k) \;=\; \Phi^{-1}\!\Bigl(1 - \frac{\alpha}{2k}\Bigr).
$$

**One asymmetry, deliberate.** The implementation floors the bar at $|t| = 2$: the
correction may only make a test harder, never easier. At $k=1$ the formula returns
$1.96$, and accepting that would let a single pre-registered test clear a bar lower than
the one every other candidate faced.

**Implementation.** [`multiplicity.py`](src/features/falsification/domain/services/multiplicity.py) —
`bonferroni_t`, `required_t` ·
**Test.** [`test_multiplicity.py`](tests/test_multiplicity.py) — `test_correction_never_lowers_the_bar`

**Result it produced — visible in the shipped ledger.**
[`data/results/ledger.json`](data/results/ledger.json) records `deflated_t_required` per
entry, and it holds **two different values: 2.91 and 2.94**. Those are
$t_{\text{req}}(14)$ and $t_{\text{req}}(15)$. The bar rose as the family grew, and the
record shows it rising.

## Šidák correction

**Status:** implemented here · **Guards against:** reporting a selected cell as if it had been the only one

**Definition.** The exact family-wise correction under independence, and the honest form
of the question for a cell picked out of a grid: not "is this cell significant" but
"would the best of $k$ cells look this good by chance".

**Derivation.** If the $k$ tests are independent and each has probability $1-p$ of *not*
reaching the observed extreme, then

$$
\Pr(\text{no test reaches it}) = (1-p)^k
\quad\Longrightarrow\quad
p_{\text{corr}} \;=\; 1-(1-p)^{k}.
$$

**Assumptions.** Independence. Real grids are correlated, so this is anti-conservative
relative to Bonferroni — which is why both are reported, and why a result that fails
Šidák has no chance of surviving anything stricter.

**Implementation.** [`multiplicity.py`](src/features/falsification/domain/services/multiplicity.py) — `sidak_p` ·
**Test.** [`test_multiplicity.py`](tests/test_multiplicity.py) — `test_sidak_reproduces_the_1021_cell_refutation`

**Result it produced.** An argmax over **1,021 filter combinations** returned
$p_{\text{corr}} = 1.0000$. The cell looked ordinarily significant on its own; corrected
for the width of the search that produced it, it is indistinguishable from picking the
best of a thousand coin-flip sequences.

## The noise ceiling — sqrt(2 ln k)

**Status:** implemented here · **Guards against:** mistaking the maximum of a search for evidence

**Definition.** Not a hypothesis test — a sanity line. It answers: at this search width,
how large a $t$ does *pure noise* produce as its maximum? A winner sitting on that line
carries no information, whatever its nominal p-value.

**Derivation sketch.** Let $Z_1,\dots,Z_k$ be i.i.d. standard normal and
$M_k = \max_i Z_i$. Using the Mills-ratio tail $1-\Phi(z) \sim \phi(z)/z$, the level
$u_k$ at which $k\bigl(1-\Phi(u_k)\bigr) \to 1$ satisfies

$$
k\,\frac{\phi(u_k)}{u_k} \approx 1
\quad\Longrightarrow\quad
\frac{u_k^{2}}{2} + \ln u_k + \ln\sqrt{2\pi} \approx \ln k
\quad\Longrightarrow\quad
u_k \approx \sqrt{2\ln k}.
$$

$M_k$ lies in the Gumbel maximum domain of attraction, and carrying the next order term
gives

$$
\mathbb{E}[M_k] \;=\; \sqrt{2\ln k} \;-\; \frac{\ln\ln k + \ln 4\pi}{2\sqrt{2\ln k}} \;+\; O\!\left(\frac{1}{\ln k}\right).
$$

The implementation uses the leading term. It is deliberately the *optimistic* bound for
the candidate: the true expected maximum is slightly below $\sqrt{2\ln k}$, so a
candidate that fails against the leading term fails against the sharper one too.

**Assumptions.** Independent tests. Correlated grids have a smaller effective width, so
the line is conservative in the direction that favours the candidate — again the safe
direction for a diagnostic whose job is to withhold belief.

**Why both this and Bonferroni.** They are not redundant, and the case that motivated
carrying both is in the tests: a result can clear its nominal bar and still sit on the
noise ceiling.

| $k$ | $\sqrt{2\ln k}$ | What happened |
|---|---|---|
| 12 | 2.23 | the hour-scan grid — [postmortem 04](docs/postmortems/04-train-selection-hour-scan.md) |
| 54–80 | 2.82–2.96 | a 92-hypothesis search returned survivors at **2.92** |
| 1,021 | 3.72 | the filter grid Šidák refuted |

The middle row is the one that matters. Nothing in the pass/fail logic objected to 2.92,
because every candidate cleared its own nominal bar. The ceiling is what caught it.

**Implementation.** [`multiplicity.py`](src/features/falsification/domain/services/multiplicity.py) —
`noise_ceiling_t`, `verdict_on_multiplicity` (requires *both* conditions) ·
**Test.** [`test_multiplicity.py`](tests/test_multiplicity.py) —
`test_noise_ceiling_matches_the_observed_incident`,
`test_a_result_can_clear_bonferroni_and_still_be_noise`

## Deflated bar rising with cumulative search

**Status:** implemented here (the bar) · quoted (deflated Sharpe, Romano–Wolf)

**Definition.** The bar is a function of everything tested so far, not of the current
test. It is fed from the ledger's cumulative count, so a candidate's threshold depends on
the search that preceded it.

**Why.** Cells were counted cumulatively as the search ran — 91, then 2,200, then 4,000 —
and $t = 3.7$ "candidates" moved from looking strong to sitting inside the noise
expectation without their own numbers changing at all.

**Related, quoted not implemented.** Deflated Sharpe (Bailey–López de Prado) appears as
results — DSR 0.65 and 0.32 against a noise floor of about 0.85 at $N=6$ trials — and
Romano–Wolf stepdown as $\text{crit}_{95} = 3.61$ against a 72-cell family. Neither is
implemented in this repository; both are defined here so the numbers can be read.

**Implementation.** [`multiplicity.py`](src/features/falsification/domain/services/multiplicity.py) ·
**Result.** Every `deflated_t_required` in [`ledger.json`](data/results/ledger.json).

## Newey–West HAC t

**Status:** quoted from the research record

**Definition.** A heteroskedasticity- and autocorrelation-consistent standard error. With
sample autocovariances $\hat\gamma_j$ and Bartlett weights $w_j = 1 - j/(L+1)$,

$$
\hat\sigma^{2}_{\text{HAC}} \;=\; \hat\gamma_0 \;+\; 2\sum_{j=1}^{L} w_j\,\hat\gamma_j,
\qquad
t \;=\; \frac{\hat\mu}{\hat\sigma_{\text{HAC}}/\sqrt{n}} .
$$

**Why it is here, and why it was not enough.** The headline number of this whole
repository is a Newey–West $t = 48.0$ across 13 consecutive profitable years. HAC
corrects for serial dependence **within a series**; it does not correct for
cross-sectional clustering of events inside a session, and it says nothing whatsoever
about whether the transacted price was obtainable.

That strategy filled 0 of 20 live orders and took 0% allocation across 32 closing
auctions. The estimator was fine. The price was not available. A correct standard error
on an unreachable return is a correct answer to the wrong question — which is the single
most useful thing in this repository.

**Appears.** [README](README.md) hero · [GRAVEYARD.md](GRAVEYARD.md) §1 ·
[postmortem 01](docs/postmortems/01-lock-fill-fantasy.md) ·
[institutional-delta](docs/institutional-delta.md) §5

## Placebo and empirical-null tests

**Status:** quoted from the research record (battery gate)

**Definition.** Recompute the statistic on a construction that should have no edge —
random entry days, shuffled volume, non-dividend names, random signals — and locate the
observed value in that null distribution.

**Estimator.** With $B$ null draws,

$$
\hat p \;=\; \frac{1 + \sum_{b=1}^{B} \mathbf{1}\{T_b \ge T_{\text{obs}}\}}{1 + B}.
$$

The $+1$ in both places is not cosmetic: it keeps $\hat p > 0$, so the report can never
claim an impossible exactness from a finite number of draws.

**What it killed.**

- **Dividend capture.** Non-dividend names returned $+1.13\%$ over the same year-end
  window. The entire "edge" was the tax calendar.
- **A best-of-150 winner** landed at the 89th percentile of its own best-of-150 null.
- **A best-of-72 gap matrix** landed between the 46th and 94th percentile of its null —
  *worse than a random winner*.
- **A random-signal null** reached in-sample Sharpe 1.45, which was at or above most of
  the "real" families in a 4,601-configuration crypto search.

## Random-rule pass rate — calibrating the test itself

**Status:** quoted from the research record

**Definition.** Feed rules that cannot work through a gate and measure the fraction that
pass. If the empirical size materially exceeds the nominal $\alpha$, every conclusion
that gate ever produced is void — not weakened, void.

**Why.** An orderbook harness was found passing arbitrary filters 64–86% of the time, and
passing random rules through a six-day leave-one-day-out at 18.5%. Both are far above
5%. See [postmortem 02](docs/postmortems/02-self-caught-look-ahead.md).

**Rule extracted.** Before trusting a new gate, measure what fraction of random rules it
admits.

## Walk-forward, frozen out-of-sample, and train/test rank correlation

**Status:** partially implemented (the audit replay ships)

**Definition.** Select parameters on a training fold, report on a test fold, and consume
the lock-box exactly once. The diagnostic that decides whether selection is defensible at
all is the correlation between train and test performance across the candidate set:

$$
\rho \;=\; \mathrm{Corr}\bigl(\text{train}_i,\ \text{test}_i\bigr),\qquad i = 1,\ldots,k .
$$

A selection criterion that correlates weakly with the objective is not a weak method — it
is close to no method.

**Result it produced.** Twelve hour configurations:
$\rho = +0.296$, the train-argmax ranked **8th of 12** on test, and **all twelve lost
money out of sample**. Reproducible in one command:

```bash
make audit
```

**Implementation.** [`scripts/replay_hour_scan.py`](scripts/replay_hour_scan.py) ·
[`data/audit/hour_scan_12_configs.json`](data/audit/hour_scan_12_configs.json) ·
**Test.** [`test_audit_artifacts.py`](tests/test_audit_artifacts.py) —
`test_train_carries_almost_no_information_about_test`

**The other half.** An earlier revision of my search code read the out-of-sample lock-box
and threw 17 mutually 0.83-correlated bets at the same fixed tail to manufacture
"OOS-robust". The fix was structural: OOS is frozen and consumed once, by a separate
finalisation step, on one pre-registered specification.

---

# 2. Pricing under microstructure constraints

**Stated plainly: nothing in this research prices a derivative.** There is no
Black–Scholes, no term structure, no volatility surface, because none was used. What is
here is pricing under the constraints of a specific market — a daily price limit, a tick
grid, single-price auctions, and a transaction tax — and those constraints turn out to
determine outcomes exactly enough that one strategy family was refuted by arithmetic
rather than by statistics.

## Tick grid and the exact limit-up price

**Status:** implemented here

**Definition.** KRX tick size is a step function of price, from 1 KRW below 2,000 to
1,000 KRW above 500,000. The daily ceiling is the $+30\%$ price *floored* onto that grid:

$$
P_{\uparrow}(P_{\text{prev}}) \;=\; \Bigl\lfloor \frac{1.30\,P_{\text{prev}}}{\delta(1.30\,P_{\text{prev}})} \Bigr\rfloor \cdot \delta(1.30\,P_{\text{prev}}) .
$$

**Why flooring matters.** Rounding instead would place the ceiling *above* the legal
limit — an order the exchange rejects, and in a backtest a fraction of a tick of free
profit on every limit-up trade. The realised ceiling premium is therefore at or slightly
below $30\%$, and it varies by name.

**Implementation.** [`market.py`](src/shared/domain/entities/market.py) — `tick_size`, `limit_up_price` ·
**Test.** [`test_market_arithmetic.py`](tests/test_market_arithmetic.py) —
`test_limit_up_is_floored_not_rounded`, boundary parametrisation over all seven bands

## The net-to-ceiling identity — determinism as a refutation

**Status:** implemented here · **This is the centrepiece of the pricing section.**

**Definition.** For a position entered at premium $e$ and exited at the ceiling premium
$c$, with entry slippage $s$ and round-trip friction $f$, the net return is not estimated
— it is computed:

$$
\text{net}(e) \;=\; \frac{1+c}{(1+e)(1+s)} \;-\; 1 \;-\; f .
$$

**Measured, not asserted.** Regressing realised net on entry premium across **298 real
orderbook trades that actually reached the ceiling**:

$$
\text{net} \;=\; 26.18 \;-\; 0.924\,e, \qquad R^{2} = 0.996 .
$$

The residual 0.4% is tick-grid variation in $c$ across names — exactly what the previous
entry predicts.

**What this refuted.** An $R^2$ of 0.996 means there is essentially no room left for a
signal. Once the entry premium is known, the outcome is known. So a strategy that
improved its limit-up lock rate from 7.7% to 77.5% through selection bands was net-
negative in **every** band: the selection improved a quantity that was not the one being
paid for. Break-even is at $e \approx +29\%$; past it, no ceiling exit pays at all.

**Note on $R^2$, used both ways in this repository.** Here $R^2 \to 1$ kills a strategy,
because determinism leaves no room for edge. In [§4](#4-panel-discipline) an $R^2 \to 0$
kills another, because the conditioning variable is unknowable at decision time. Neither
direction is inherently good news.

**Implementation.** [`market.py`](src/shared/domain/entities/market.py) ·
**Test.** [`test_market_arithmetic.py`](tests/test_market_arithmetic.py) —
`test_ceiling_return_is_determined_by_entry_premium_alone`,
`test_entry_premium_above_break_even_is_unrecoverable`

## Auction allocation and the fill-feasibility predicate

**Status:** implemented here

**Definition.** KRX opens and closes with single-price call auctions. Allocation is
pro-rata in queue depth at the clearing price:

$$
\text{allocation} \;\approx\; \frac{q_{\text{order}}}{Q_{\text{queue}}} \cdot Q_{\text{available}} .
$$

At the limit price the buy queue is enormous by construction — everyone who wants the
name wants it at exactly that price, and nobody is selling into a print bid to the cap —
so $q/Q \to 0$ for a retail-sized order. Measured: **0% allocation across 32 auctions.**

**Three conditions, one strategy killed by each.**

| Condition | What it means | What it killed |
|---|---|---|
| `LIMIT_LOCKED` | price at or above the daily ceiling | the $t = 48.0$ strategy: 0 of 20 orders filled |
| `NO_OPPOSING_SIZE` | no resting size on the side being crossed | the ask is *zero*, not missing — a naive `ask > 0` filter once cut a sample from 69 events to 3 and produced a fabricated negative |
| `SIGNAL_AFTER_PRICE` | signal confirms at or after the price it transacts at | a futures fade at $t = 3.71$ became $t = 1.34$ on the first executable leg |

**Implementation.** [`fill_feasibility.py`](src/features/execution_realism/domain/services/fill_feasibility.py) — `check_buy` ·
**Test.** [`test_refusals.py`](tests/test_refusals.py) —
`test_a_limit_up_locked_close_is_not_a_buyable_price`,
`test_zero_ask_size_is_locked_not_merely_thin`,
`test_a_signal_confirmed_at_the_price_it_transacts_is_a_ghost_leg`

## Participation rate and partial-fill capacity

**Status:** implemented here

**Definition.** Participation is the order's share of the volume it actually competes
with:

$$
\pi \;=\; \frac{V_{\text{order}}}{V_{\text{window}}}, \qquad V_{\text{window}} > 0 .
$$

**The denominator is the entire content of this entry.** A backtest measured participation
against *daily* volume for an order entering an *auction*. Against the correct
denominator, capacity was overstated by roughly **69×**, about 30% of the sample violated
a 10% participation cap outright, and enforcing that cap cut realised value per trade by
**39%**.

A window with no volume raises rather than dividing: zero traded value is a fill problem,
not a division problem.

**Partial fills.** The simulated broker accumulates a volume-weighted average across
partial executions,

$$
\bar P \;=\; \frac{\sum_j q_j P_j}{\sum_j q_j},
$$

and one quote's offered size is a single pool drained across all resting orders, not a
fresh allowance handed to each.

**Implementation.** [`fill_feasibility.py`](src/features/execution_realism/domain/services/fill_feasibility.py) — `participation_rate` ·
[`simulated_broker.py`](src/features/execution/infrastructure/simulated_broker.py) ·
**Test.** [`test_execution.py`](tests/test_execution.py), [`test_refusals.py`](tests/test_refusals.py)

## The cost floor, and why break-even is endogenous

**Status:** implemented here (as a constant the candidate cannot reach)

**Definition.** $\text{net} = \text{gross} - f$ with $f = 0.38\%$ — a 0.20% sell-side
securities transaction tax (raised from 0.15% in 2026) plus brokerage. Judged at this
fixed value regardless of what a candidate's own specification proposes.

**Why it is not a parameter.** An earlier revision of my search code **lowered its own
declared cost to 0.25** in order to produce a `retail_tradable` verdict.
Cost is no longer an input the candidate controls.

**The subtler failure — break-even is endogenous to selection.** Suppose a strategy wins
$w$ on a hit and loses $\ell$ otherwise, with hit rate $h$. Break-even is

$$
h^{\ast} \;=\; \frac{\ell + f}{w + \ell} .
$$

Treating $h^{\ast}$ as a constant and improving $h$ looks like progress. It is not: the
features that raise $h$ also deepen $\ell$. In one measured case selection lifted the
lock rate from 4% to 8.5% while non-lock fades worsened from $-1.67\%$ to $-3.0/-3.8\%$,
which pushed $h^{\ast}$ from 7.6% to about **11.3%** — above the 8.5% achieved. The two
effects cancelled.

**Rule extracted.** Never plug a global break-even into a selected subset. Measure the
subset's net directly.

**Implementation.** [`market.py`](src/shared/domain/entities/market.py) — `ROUND_TRIP_COST_PCT`

## Adverse selection in the filled set

**Status:** quoted from the research record (the size ladder is withheld — see [DISCLOSURE](docs/DISCLOSURE.md))

**Definition.** The estimand is not the fill rate but the *conditional outcome given
fill*. Compare ideal net on the orders that could not be filled against realised net on
those that were:

$$
\Delta \;=\; \mathbb{E}\bigl[x \mid \text{unfilled}\bigr] \;-\; \mathbb{E}\bigl[x \mid \text{filled}\bigr].
$$

**Result.** 87 unfilled orders had an ideal net of $+2.23\%$ against $+0.70\%$ for the
220 that filled. The trades you cannot get are roughly three times better than the ones
you get, and the fill rate falls fastest on exactly the names the signal selects — so
adverse selection worsens with size rather than staying constant.

**The finding this generalises to.** Fillability and edge are mutually exclusive here,
confirmed independently six times across three markets. It is why every fill claim is
checked before any significance test is reported.

---

# 3. Risk decomposition and stopping

## Beta separation

**Status:** implemented here

**Definition.** Raw return answers "did capital survive". Excess answers "is the strategy
working". They are different questions and a stop rule that conflates them sells the
bottom.

$$
\text{excess} \;=\; r \;-\; \beta\, r_m .
$$

**Three conventions, each doing work.**

1. **$\beta$ is set below the measured exposure.** Crediting less of a drawdown to the
   market makes the "blame the market" defence harder to invoke, not easier. Conservatism
   here means being harsh on your own strategy.
2. **A missing index observation propagates as `None`, never as $0$.** Zero would make
   excess equal raw and silently disable the mechanism at exactly the moment the index
   feed is failing — which is when it is most needed.
3. **Boards are separated.** KOSPI and KOSDAQ carry distinct index series; a single
   "market return" would misattribute one board's drawdown to the other.

**Sign-flip robustness — reporting $\beta^{\ast}$ instead of defending a point estimate.**
The excess changes sign at

$$
\beta^{\ast} \;=\; \frac{r}{r_m},
$$

measured at $\beta^{\ast} = 0.040$ for the basket in question. Arguing about whether the
true beta is 0.5 or 0.8 or 1.0 is irrelevant when the conclusion holds for any
$\beta > 0.04$. Reporting the flip point converts a contestable estimate into an
uncontestable one.

**The rescue it performed.** A small negative raw forward return, inside a market down
several times as far over the same holding periods, becomes a positive excess. Judged on raw alone,
a working strategy would have been retired during a crash — and a rule that fires hardest
exactly when markets fall is not a risk control, it is a mechanism for selling the bottom.

**Implementation.** [`beta_separation.py`](src/features/falsification/domain/services/beta_separation.py) ·
**Test.** [`test_equivalence_with_production.py`](tests/test_equivalence_with_production.py) — 22 assertions
pinning `index_return` against the production original, including the previous-trading-day
fallback and the None-never-zero semantics

## The two-condition stop rule

**Status:** implemented here

**Definition.** A stop requires **both** raw and excess to breach the threshold.

$$
\text{stop} \;=\; \bigl[\, r \le \tau \,\bigr] \;\wedge\; \bigl[\, \text{excess} \le \tau \,\bigr].
$$

A raw-only breach is a market event: reported, attributed, and **not** acted on. An
excess that cannot be measured — index data missing above a tolerance — resolves to
*stop*, because an unmeasurable excess must not become a free pass.

**Implementation.** [`beta_separation.py`](src/features/falsification/domain/services/beta_separation.py) — `should_stop`

## Kill lines as sigma-over-root-n quantile curves

**Status:** derivation published · **values withheld** ([DISCLOSURE](docs/DISCLOSURE.md))

**Definition.** Thresholds pre-registered at several forward sample sizes, set from the
pre-freeze return distribution before any forward observation exists:

$$
L(n) \;=\; \hat\mu_{\text{pre}} \;-\; z_q\,\frac{\hat\sigma_{\text{pre}}}{\sqrt{n}} .
$$

The $1/\sqrt{n}$ shape is the content: an early sample is allowed to wander much further
below the mean before it means anything, and a fixed threshold applied at every $n$ would
either kill good strategies early or never kill bad ones.

**Kill lines only. There is no confirm line anywhere, by construction.** `FrozenRule` has
no settable confirm threshold, because a field that could hold one would eventually hold
one. Forward testing here is a catastrophe detector, not a confirmation device.

**Why the four values are withheld, stated as algebra.** A set of thresholds indexed by
$n$ *is* a $\sigma/\sqrt{n}$ curve. Two points determine $\hat\sigma_{\text{pre}}$;
combined with a published mean the whole return distribution falls out. The same identity

$$
t \;=\; \frac{\bar x \sqrt{n}}{\hat\sigma}
$$

is why the disclosure rule is **any two of mean, $t$, $n$ — never all three**. Publishing
the third is publishing $\hat\sigma$.

**Implementation.** [`frozen_rule.py`](src/features/preregistration/domain/entities/frozen_rule.py) —
`JudgmentCriteria.kill_thresholds`; `confirm_threshold` returns `None` unconditionally ·
**Test.** [`test_refusals.py`](tests/test_refusals.py) — `test_there_is_no_confirm_line`,
`test_a_frozen_rule_cannot_be_mutated`

## Power, and choosing an answerable question

**Status:** quoted from the research record

**Definition.** Sessions required for a day-clustered $t$ to exceed 2 at effect $\mu$ and
session dispersion $\sigma$:

$$
n^{\ast} \;=\; \left(\frac{2\sigma}{\mu}\right)^{2}.
$$

**What it settled.** For the confirmed strategy this is on the order of a thousand trades
— roughly seven and a half years at its event rate. Forward testing therefore *cannot*
confirm it, which is why the pre-registered lines are kill lines only and why an early
negative forward reading at a single-digit sample excludes almost nothing.

**The rule it produced.** Never choose a test that needs data you cannot get. A
distributional duel required about 2,844 market-days at the observed paired dispersion;
a narrower question about the same phenomenon was decidable at $n = 34$. Picking the
answerable question is a modelling decision, not a compromise.

---

# 4. Panel discipline

## Survivorship recomputation as a paired comparison

**Status:** quoted from the research record — the *design* is the content

**Definition.** Recompute the identical statistic on a panel that includes delisted
names, and report the change as a ratio of itself:

$$
\text{sensitivity} \;=\; \frac{\text{edge}_{\text{survivors only}} - \text{edge}_{\text{full panel}}}{\text{edge}_{\text{survivors only}}} .
$$

**Why a ratio, and why a pair.** A single "it passed" is nearly worthless. The evidence is
the *asymmetry* under an identical check: one strategy moved by under 5% of itself while
a gap-down strategy tested in the same session, on the same data, with the same recompute,
lost 60% and ended sign-undetermined. Ratios also disclose no levels, which is what lets
this comparison appear in a redacted document at all.

**The delisting price signature.** Korean delisting is not a gentle decline but
*crash → halt → liquidation*, so a walk-forward screen on a few price observables removes
about three quarters of traps. It is worth nothing as alpha, and that is the publishable
part: among surviving names only, filtered performance $\approx$ a random subsample of the
same size ($p = 0.72$) and slightly *below* unfiltered. It is mine-clearing, not edge —
and conflating the two is how a risk control gets sold as a strategy.

**An impossibility result, recorded because it constrains everything downstream.** The
flow panel and the delisted panel do not join. Small-cap flow strategies therefore
**cannot in principle** be survivorship-tested with available data — which is a fact about
the data, not a gap in the effort.

## Unobservable at decision time

**Status:** quoted from the research record

**Definition.** A strategy conditions on a variable that does not exist when the order
must be placed. Regress the realised conditioning variable on the value observable at
decision time:

$$
X_{\text{realised}} \;=\; a + b\,X_{\text{observable}} + \varepsilon .
$$

**Result.** $R^{2} = 0.001$, $\rho = -0.046$, $n = 126$. Expected $-7.02\%$ against
realised $-2.42\%$, and **15 of 15 shallower**. The information is destroyed precisely at
the single-price determination: indicative-to-indicative $R^2 = 0.445$, but
indicative-to-realised $R^2 = 0.046$.

**Consequence, and why it is not ordinary look-ahead.** No code was wrong. The
information simply does not exist yet. The 14 of 14 unfilled live orders were the design
working, not breaking — see the next entry.

## Binomial arithmetic on consecutive events

**Status:** quoted from the research record

**Definition.** Given a per-attempt fill probability $p$, the chance of $m$ consecutive
misses is $(1-p)^m$.

**Result.** With $P(\text{realised} \le -7\% \mid \text{expected} \le -7\%) = 7.3\%$,

$$
(1 - 0.073)^{14} \;=\; 0.348 .
$$

Fourteen consecutive unfilled orders had a **34.8%** chance of occurring — statistically
ordinary. Without this calculation the run reads as an implementation bug and someone
goes looking for one.

**The same arithmetic elsewhere.** In a 276-combination sweep, 2 combinations reached
$t > 2$. At $\alpha = 0.05$ the expectation under the null is $276 \times 0.05 \approx 14$;
observing 2 is *fewer* than chance.

## Bootstrap under a contaminated sample

**Status:** quoted from the research record · [postmortem 05](docs/postmortems/05-monte-carlo-selected-winner.md)

**Definition.** Resampling with replacement estimates the sampling distribution of a
statistic — *of the population the sample was drawn from*. If the sample was selected on
outcome, the bootstrap faithfully estimates the distribution of the selection.

**The artifact, and why it ships.** A Monte Carlo over post-hoc-selected winning trades
reported a mean of $+2175\%$, a 5th percentile of $+1939\%$, and $P(\text{loss}) = 0.00$.
The file contains its own control: the unfiltered arm shows $P(\text{loss}) = 0.50$ and a
5th percentile of $-98\%$. The delta between the two rows *is* the selection effect,
measured, in one file.

**Range checks precede validation.** $P(\text{loss}) = 0$ is outside the range a correct
calculation can produce. Checking whether a result is *possible* is cheaper than any
validation and catches what validation cannot, because validation assumes the sample is
honest.

**Test.** [`test_audit_artifacts.py`](tests/test_audit_artifacts.py) —
`test_filtered_arm_claims_certainty_and_that_is_the_tell`

## Allocation between books — dominance, not optimisation

**Status:** quoted from the research record

**Definition.** With several candidate books and one capital pool, the question asked was
not the optimal weight vector but a weaker and far more answerable one: **is any candidate
allocation dominated?** Allocation $A$ dominates $B$ if $A$'s realised path is at least as
good as $B$'s at every evaluation date and strictly better at one. Dominance requires no
covariance estimate — which is the point, because with this few books the covariance matrix
would have more free entries than the sample can support.

**Result.** An inverse-weighting scheme — more capital to the book with the weaker recent
record — was **completely dominated** by flat weighting over the replay. Completely, in the
technical sense: not worse on average, worse at every evaluation date. That settles a
direction without claiming an optimum, and at this sample size a direction is the strongest
honest claim available.

**Arrival-day replay.** Allocations are evaluated on the day capital actually arrives, not
the day the signal fires. The two differ whenever a book's events cluster, and evaluating on
signal day quietly assumes capital that was in fact already committed elsewhere.

**Book arithmetic, and why it does not rescue the problem.** For $N$ books at equal weight
with pairwise uncorrelated returns of common volatility $\sigma$,

$$
\sigma_{\text{book}} \;=\; \frac{\sigma}{\sqrt{N}} ,
$$

so the benefit of adding books grows as $\sqrt{N}$, not $N$. With $N$ small *and* the
survivors sitting inside one mechanism class, the independence assumption that buys even
$\sqrt{N}$ is the first thing to fail. That is the actual argument for staying at equal
weight: the arithmetic justifying anything cleverer needs an input this sample cannot supply.

**Withheld:** ticket sizes and governor constants. The method is the publishable part.

---

## What is not modelled here

Stated so the boundary is explicit rather than left for a reader to infer:

- **No derivatives pricing.** No option pricing, no term structure, no volatility surface.
  The instruments in this research are cash equities plus, in the graveyard, futures and
  perpetuals that were never traded.
- **No portfolio optimisation.** No mean-variance, no efficient frontier, no covariance
  estimation — with the survivors inside a single mechanism class, the covariance matrix
  would have more free parameters than the sample supports. What was done instead is the
  dominance check above, which needs no covariance estimate; sizing stays equal-weight with
  a participation cap.
- **No factor model.** Beta separation uses a single market factor. There is no
  Fama–French decomposition, because the question was never attribution — it was whether
  the residual survived friction.
- **No machine learning in this repository.** Gradient-boosted models exist in the
  original research and are referenced in the graveyard; none ships here, and the
  strategy port deliberately holds a refuted rule and a random control instead. The
  one worth reading about is written up rather than shipped:
  [postmortem 07](docs/postmortems/07-the-most-accurate-model-was-unusable.md), where
  a walk-forward AUC of 0.909 turned out to be the wrong metric because the model's
  discrimination and the reachable order set did not intersect.

The omissions are the honest shape of the work. Adding a model that nothing used would
make this document decorative, which is the one thing it is designed not to be.
