# Verification

Every claim in this repository that can be checked mechanically, and the command that checks it.

## The production system is real, and this repository did not change it

Research logic in `src/` was **copied** out of a live operational file running real money on KRX.
It was never edited by this repository. The copy is pinned to the original by equivalence tests.

| Gate | What it checks | Result |
|---|---|---|
| 1 | Production suite, **before** this repository existed | **364 passed** |
| 2 | This repository's suite, at the time the gates ran | **98 passed** |
| 3 | Production suite, **after** all work here | **364 passed** — identical count |
| 4 | Equivalence: port vs original on shared fixtures | **22 of the 98**, all green |

This repository's suite has since grown to **401**; production has not been touched again by this
work, and the gate above is left at the number it actually ran with rather than restated.

```
app_ec2.py sha256 during gates 1-3
  1ca55f0b73b0d9394b66e5f50100cfbdd9d22f0b6e675dd2ea1ba1c3f19c899f   (identical before and after)
```

Gate 4 imports the live file by path and asserts identical output — including the
previous-trading-day fallback and the `None`-never-zero semantics for a missing index. It skips
cleanly when the production file is absent, and **the skip itself is asserted**, so the block
cannot silently disappear on a machine that lacks it.

### A later, separate change to production

After this repository was complete, the production system was modified in an unrelated piece of
work: credential loading was consolidated onto a single source outside every repository, replacing
five divergent inline loaders — one of which crashed rather than returning empty when its config
file was missing. That change added 12 regression tests.

```
production suite  364 passed  ->  376 passed   (+12 new tests, 0 broken)
app_ec2.py sha256 changed, as it must have
```

It is recorded here rather than folded into the gates above because **conflating the two would
make the "identical before and after" claim false.** The claim in gates 1-3 is about this
repository's work specifically, and it stays exact.

## Structural audits

| Check | Result |
|---|---|
| `git remote -v` | points at the private working remote. Publication is a separate export — see below |
| Secret-shaped paths across all tracked files | 0 |
| Credential-shaped strings in tracked content | 0 |
| `infrastructure` imports inside `domain/` modules | 0 |
| `os` / network imports inside domain services | 0 |
| Clean clone → `make verify` | green |
| Same suite on CI, Ubuntu, `TZ=UTC` | green — [`verify.yml`](.github/workflows/verify.yml) |

## Publication

This is the published repository. It was created by exporting a single tree from a private working
repository rather than by making that one public, because the working history predates the current
disclosure tier — earlier commits there contain material these documents withhold.

The history here therefore **begins at the export**. That boundary is the redaction, not an absence
of work.

## Redaction audit

Three strategies are live. The audit runs over **every tracked file** — prose, source, tests,
fixtures, JSON and figures — not over the documents alone.

| Check | Result |
|---|---|
| Withheld values or names anywhere in the tree | 0, enforced by a blocklist held outside this repository |
| Real trading dates in any test fixture | 0 — fixtures use synthetic levels on fictional dates |
| Calendar axis on any live-strategy figure | none exist |
| Reconstruction test: can a reader state the entry rule, exit rule or filter set of a live strategy from this repository alone? | no |

**This audit was wrong once, and the correction is the reason it is scoped the way it is.** An
earlier version looked only at prose. The values it existed to protect were sitting in a test file
— as the regex literals of the guard itself — in a docstring constant, and in a JSON key. A test
that asserts a value does not appear publishes that value, so the list now lives outside the tree
and the check fails rather than skips when it cannot be found. The blocklist is not a document that
can be made safe by care; it had to stop being a document in this repository at all.

The policy behind these is [docs/DISCLOSURE.md](docs/DISCLOSURE.md).

## Re-run it yourself

```bash
make audit      # replays a documented finding. stdlib only, no credentials, ~1s
make setup      # only needed for the full suite
make verify     # 401 tests
```

```bash
# and, on a machine that has the production system:
cd ~/kosdaq_paper && python -m pytest      # must print 376 passed
```

Without that machine, the equivalence block fails rather than skipping — deliberately, so lost
coverage is visible. `ALLOW_MISSING_PRODUCTION=1` is the explicit opt-out, and CI is the only place
it is set.
