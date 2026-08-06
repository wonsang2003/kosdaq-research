# Institutional delta

Six strategies in the graveyard are not dead. They are dead **for a retail account in Korea**.

For each one: the constraint that killed it, and the capability that reverses it. The measurements
behind each constraint are withheld on the same policy as the rest of the graveyard — what
transfers is the mapping from constraint to capability, not my numbers. See
[DISCLOSURE.md](DISCLOSURE.md).

---

## 1. Overnight cross-sectional excess

| | |
|---|---|
| Killed by | **Cost floor.** A market-neutral, highly significant, decade-spanning excess that is smaller than the sell-side transaction tax. Retail net is negative at *zero* slippage |
| Reverses at | A participant whose effective per-turn cost sits below the excess. Also decaying, so the window is closing regardless |
| Honest caveat | Cross-sectional overnight effects are well documented in the literature. This is the cleanest demonstration of cost-floor discipline in the repository, not a novel discovery |

## 2. Market-neutral large-cap construction

| | |
|---|---|
| Killed by | **Capital.** The hedged form needs several times the available account — a derivatives minimum, index-future margin, and one single-stock-future contract |
| Killed again by | **Execution decay.** Five-minute data showed most of the alpha gone almost immediately. Entry at the open is a phantom leg; entry later leaves nothing after cost. This second death applies to any participant, and it is the more important finding |
| Reverses at | An account above the derivatives minimum **and** sub-minute execution. Capital alone is not sufficient — the second death is not about size |

## 3. Small-cap short

| | |
|---|---|
| Killed by | **Borrow inventory.** A live lendable-securities query returned no inventory on this board, and almost none among the names the signal actually selects |
| Reverses at | A borrow desk with small-cap inventory. The overlap is the whole problem: borrow is not expensive here, it does not exist |
| Note | An earlier claim of mine that this was impossible through the broker API was **wrong and corrected** — the automation path exists and works. The blocker is inventory, not capability |

## 4. Composite index short

| | |
|---|---|
| Killed by | **No instrument.** No future is written on the composite. The listed derivative covers a large-cap subindex, and most of the alpha sits outside it |
| Reverses at | A swap or basket against the composite. Structurally available to a desk, structurally unavailable to an account |
| Why it is interesting | The alpha is trapped in names that are, by index-construction rules, permanently ineligible for the only listed instrument |

## 5. Limit-up close, next-open

| | |
|---|---|
| Killed by | **Auction allocation.** No allocation across the live attempts. At the limit price the buy queue is enormous by construction, and pro-rata allocation to a retail order rounds to nothing |
| Reverses at | Auction priority and size. Nothing in the analysis says the effect is not real for a member firm — the finding is specific to a small order's place in the queue |
| Falsifier | The live sample is small. A hundred-plus attempts with recorded allocation ratios would settle it. [Full postmortem](postmortems/01-lock-fill-fantasy.md) |

## 6. Cross-market lead-lag against a listed perpetual

| | |
|---|---|
| Killed by | **Cost floor.** Even the most predictable decile is a fraction of the round trip. Every configuration is negative at zero spread, because the sell-side tax alone exceeds the signal |
| Reverses at | A tax-exempt participant, or a venue pairing where one leg avoids the transaction tax |
| Also measured | The direction is the opposite of the intuition, and the asymmetry across underlyings is the proof: globally traded names lead, domestic-only names do not |

---

## What this table is for

Read one way, this is a list of things that did not work. Read correctly, it is a specification of
what capability set converts each one into a live strategy — a borrow desk with small-cap
inventory, sub-minute execution, auction priority, a lower effective tax rate, or a swap against an
unlisted composite.

Every entry is a constraint I measured, not one I complained about. The mapping is the part that
transfers; the measurements stay with me.
