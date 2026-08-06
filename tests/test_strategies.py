"""Strategy implementations, and the boundary the alpha does not cross.

The first test in this file is the important one. Everything else checks that the two
shipped implementations honour the port; `test_no_registered_strategy_claims_to_work`
checks that nothing else is ever shipped alongside them. It is the executable form of
`docs/DISCLOSURE.md`: if a confirmed rule is ever registered, the build fails before
the commit that leaked it can be pushed.

The rest of the file follows this repository's habit of testing refusals — an empty
result and a raised error are different observations, and most of the assertions here
are about keeping them different.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.replay_hour_scan import load_configs
from src.features.market_data.domain.entities.bar import Bar
from src.features.strategy.domain.entities.signal import Side, Signal
from src.features.strategy.domain.repositories.strategy import Strategy, StrategyError
from src.features.strategy.infrastructure.random_control import RandomControl
from src.features.strategy.infrastructure.refuted_hour_rule import (
    SELECTED_HOUR,
    RefutedHourRule,
)
from src.features.strategy.infrastructure.registry import (
    DEFAULT_STRATEGY_KEY,
    STRATEGIES,
    UnknownStrategy,
    get_strategy,
)
from src.shared.domain.clock import MARKET_TZ

REPO = Path(__file__).resolve().parents[1]

# A Monday, inside the session, inside the hour the scanner selected on train.
AT_THE_HOUR = datetime(2026, 6, 15, SELECTED_HOUR, 0, tzinfo=MARKET_TZ)
OFF_HOUR = AT_THE_HOUR.replace(hour=10)


def bars(
    symbol: str,
    as_of: datetime = AT_THE_HOUR,
    *,
    count: int = 21,
    baseline: float = 1_000_000.0,
    latest: float | None = None,
) -> list[Bar]:
    """One-minute bars ending exactly at `as_of`, chronological, all at or before it.

    `latest` is the final bar's traded value and `baseline` every earlier one, so a
    test can state a relative-turnover ratio directly instead of computing one.
    """
    latest = baseline if latest is None else latest
    out = []
    for i in range(count):
        value = latest if i == count - 1 else baseline
        out.append(
            Bar(
                symbol=symbol,
                timestamp=as_of - timedelta(minutes=count - 1 - i),
                interval_minutes=1,
                open=1_000, high=1_010, low=990, close=1_005,
                volume=value / 1_005,
                traded_value=value,
            )
        )
    return out


# ── the alpha-protection boundary ────────────────────────────────────────────
def test_no_registered_strategy_claims_to_work():
    """The one test this file exists for.

    The confirmed rules run on the author's own capital and are not here. Every
    registered implementation must declare itself REFUTED or CONTROL — never
    CONFIRMED — so that a reader can verify the claim by running the suite rather than
    by believing `docs/DISCLOSURE.md`.
    """
    assert STRATEGIES, "an empty registry would pass this test vacuously"
    for key, factory in STRATEGIES.items():
        spec = factory().spec
        assert spec.verdict in {"REFUTED", "CONTROL"}, (
            f"{key} declares {spec.verdict!r}; publishing a confirmed strategy is the "
            "one thing docs/DISCLOSURE.md forbids"
        )
        assert spec.verdict != "CONFIRMED"
        assert spec.is_expected_to_lose


def test_the_default_strategy_is_a_documented_failure():
    """Running this repository with nothing configured must produce the demonstration,
    not something that reads as an offer."""
    assert get_strategy(DEFAULT_STRATEGY_KEY).spec.verdict == "REFUTED"


def test_registry_keys_match_the_specs_they_build():
    """A key that disagrees with `spec.key` makes the journal unjoinable to the config
    that produced it, which is only discovered while reading a bad day's records."""
    for key, factory in STRATEGIES.items():
        assert factory().spec.key == key
        assert isinstance(get_strategy(key), Strategy)


def test_an_unknown_key_raises_and_names_the_available_ones():
    """A bare KeyError from a config lookup sends the reader to the wrong file."""
    with pytest.raises(UnknownStrategy) as excinfo:
        get_strategy("momentum_v3_final")
    message = str(excinfo.value)
    assert "momentum_v3_final" in message
    for key in STRATEGIES:
        assert key in message
    assert "DISCLOSURE" in message


def test_an_unknown_key_is_not_mistaken_for_a_bad_trading_day():
    """`UnknownStrategy` is deliberately not a `StrategyError`: a caller wrapping its
    evaluation loop in `except StrategyError` must not swallow a typo'd config key."""
    assert not issubclass(UnknownStrategy, StrategyError)


def test_the_banner_warns_that_losing_is_the_expected_result():
    """The runtime tells the reviewer, so the document does not have to be found."""
    for factory in STRATEGIES.values():
        banner = factory().describe()
        assert "documented as not working" in banner
        assert "expected result" in banner


def test_evidence_points_at_a_file_that_exists():
    """A verdict with a dangling citation is an assertion, not evidence."""
    for factory in STRATEGIES.values():
        spec = factory().spec
        assert spec.evidence, f"{spec.key} cites nothing"
        assert (REPO / spec.evidence).exists(), spec.evidence


# ── the refuted rule reproduces the finding ──────────────────────────────────
def test_the_shipped_hour_is_the_train_argmax_from_the_audit_file():
    """The rule hardcodes 13 because 13 won on train — re-derived here from the
    scanner's own output so the code and the artifact cannot drift apart."""
    rows = load_configs()
    picked = max(rows, key=lambda r: r["train"])
    assert picked["label"] == str(SELECTED_HOUR)
    assert picked["test"] < 0
    assert RefutedHourRule().hour == SELECTED_HOUR


def test_the_spec_names_the_finding_and_cites_the_postmortem():
    spec = RefutedHourRule().spec
    assert spec.verdict == "REFUTED"
    assert "-0.854" in spec.summary
    assert "12" in spec.summary
    assert spec.evidence == "docs/postmortems/04-train-selection-hour-scan.md"


def test_ranks_are_one_to_n_with_no_gaps():
    """Rank is the only ordering downstream sizing has. A gap or a repeat silently
    changes position sizes rather than raising anywhere."""
    universe = {f"00000{i}": bars(f"00000{i}", latest=1_000_000.0 * (i + 1)) for i in range(5)}
    signals = RefutedHourRule().evaluate(AT_THE_HOUR, universe, max_signals=5)
    assert [s.rank for s in signals] == [1, 2, 3, 4, 5]
    assert len({s.symbol for s in signals}) == 5


def test_every_signal_is_stamped_with_the_decision_instant():
    """Not the bar time. A fill priced at or before the signal's own timestamp is a
    ghost leg, and the downstream feasibility check can only catch that if the stamp
    is the decision instant."""
    universe = {"000001": bars("000001", latest=5_000_000.0)}
    signals = RefutedHourRule().evaluate(AT_THE_HOUR, universe, max_signals=3)
    assert signals
    assert all(s.timestamp == AT_THE_HOUR for s in signals)
    assert all(s.side is Side.BUY for s in signals)
    assert all(isinstance(s, Signal) for s in signals)


def test_ranking_is_relative_turnover_not_absolute_size():
    """All three names print the same notional in the final bar; only their own
    trailing norms differ. An absolute ranking is a market-cap ranking in disguise and
    would return the same handful of names every hour of every day."""
    universe = {
        "AAA": bars("AAA", baseline=1_000_000.0, latest=5_000_000.0),   # 5x
        "BBB": bars("BBB", baseline=500_000.0, latest=5_000_000.0),     # 10x
        "CCC": bars("CCC", baseline=5_000_000.0, latest=5_000_000.0),   # 1x
    }
    signals = RefutedHourRule().evaluate(AT_THE_HOUR, universe, max_signals=3)
    assert [s.symbol for s in signals] == ["BBB", "AAA", "CCC"]


def test_a_name_with_no_trailing_turnover_is_not_infinitely_interesting():
    """Zero trailing turnover makes the ratio undefined, not enormous. Substituting
    infinity would sort the least-traded names to rank 1 — the exact inversion of the
    rule — and the ranking would still look perfectly ordinary."""
    universe = {
        "AAA": bars("AAA", baseline=1_000_000.0, latest=2_000_000.0),
        "DEAD": bars("DEAD", baseline=0.0, latest=1_000_000.0),
        "THIN": bars("THIN", count=3, baseline=1_000_000.0, latest=9_000_000.0),
    }
    signals = RefutedHourRule().evaluate(AT_THE_HOUR, universe, max_signals=5)
    assert [s.symbol for s in signals] == ["AAA"]
    assert [s.rank for s in signals] == [1]


def test_the_cap_is_honoured():
    universe = {f"00000{i}": bars(f"00000{i}", latest=1_000_000.0 * (i + 1)) for i in range(6)}
    signals = RefutedHourRule().evaluate(AT_THE_HOUR, universe, max_signals=2)
    assert len(signals) == 2
    assert [s.rank for s in signals] == [1, 2]


def test_the_ranking_does_not_depend_on_dict_insertion_order():
    """A backtest and a live loop build this dict from different sources. If order
    leaked into the ranks the two would size the same day differently, and nothing
    would raise."""
    forward = {s: bars(s, latest=2_000_000.0) for s in ("AAA", "BBB", "CCC")}
    reversed_ = {s: forward[s] for s in reversed(list(forward))}
    rule = RefutedHourRule()
    assert [s.symbol for s in rule.evaluate(AT_THE_HOUR, forward, 3)] == [
        s.symbol for s in rule.evaluate(AT_THE_HOUR, reversed_, 3)
    ]


# ── empty is a result; failure is not ────────────────────────────────────────
def test_outside_the_hour_an_empty_list_is_a_result_not_an_error():
    """The rule declined to look. That is the whole rule, and it must not raise."""
    universe = {"AAA": bars("AAA", OFF_HOUR, latest=9_000_000.0)}
    assert RefutedHourRule().evaluate(OFF_HOUR, universe, max_signals=5) == []


def test_an_empty_universe_is_a_result_not_an_error():
    assert RefutedHourRule().evaluate(AT_THE_HOUR, {}, max_signals=5) == []
    assert RandomControl().evaluate(AT_THE_HOUR, {}, max_signals=5) == []


@pytest.mark.parametrize("strategy", [RefutedHourRule(), RandomControl()])
def test_a_bar_after_the_decision_instant_raises_instead_of_returning_nothing(strategy):
    """Look-ahead is the one defect that never announces itself — it produces a better
    result, not an error. Returning `[]` here would report a quiet day while the
    upstream truncation was broken."""
    tainted = bars("AAA")
    tainted.append(
        Bar(symbol="AAA", timestamp=AT_THE_HOUR + timedelta(minutes=1), interval_minutes=1,
            open=1_000, high=1_010, low=990, close=1_005, volume=1.0, traded_value=9_000_000.0)
    )
    with pytest.raises(StrategyError, match="look-ahead"):
        strategy.evaluate(AT_THE_HOUR, {"AAA": tainted}, max_signals=5)


def test_duplicated_rows_raise_rather_than_shifting_a_turnover_average():
    """A duplicated row set in the loader once fabricated a +1.5%/day edge here, and
    every downstream test was correct about the data it was given."""
    doubled = bars("AAA")
    doubled.append(doubled[-1])
    with pytest.raises(StrategyError, match="strictly increasing"):
        RefutedHourRule().evaluate(AT_THE_HOUR, {"AAA": doubled}, max_signals=5)


@pytest.mark.parametrize("strategy", [RefutedHourRule(), RandomControl()])
def test_a_naive_decision_instant_raises(strategy):
    """A naive `as_of` does not merely look untidy — the hour gate is exchange-local,
    so it would select the wrong hour on a UTC host and log nothing."""
    naive = AT_THE_HOUR.replace(tzinfo=None)
    with pytest.raises(StrategyError, match="timezone-aware"):
        strategy.evaluate(naive, {"AAA": bars("AAA")}, max_signals=5)


@pytest.mark.parametrize("strategy", [RefutedHourRule(), RandomControl()])
def test_a_negative_cap_is_a_caller_bug_and_raises(strategy):
    with pytest.raises(StrategyError, match="max_signals"):
        strategy.evaluate(AT_THE_HOUR, {"AAA": bars("AAA")}, max_signals=-1)


def test_a_nonsensical_configuration_raises_at_construction():
    """At construction rather than at the first evaluation, so a misconfigured run
    cannot be mistaken for a market that offered nothing."""
    with pytest.raises(StrategyError, match="hour"):
        RefutedHourRule(hour=25)
    with pytest.raises(StrategyError, match="lookback"):
        RefutedHourRule(lookback_bars=0)


def test_the_hour_gate_is_exchange_local_not_host_local():
    """13:00 KST is 04:00 UTC. Stated in UTC deliberately: if the conversion were ever
    dropped, this fails rather than quietly passing on a machine set to Seoul time."""
    in_hour = datetime(2026, 6, 15, 4, 0, tzinfo=timezone.utc)
    out_of_hour = datetime(2026, 6, 15, SELECTED_HOUR, 0, tzinfo=timezone.utc)  # 22:00 KST
    rule = RefutedHourRule()
    assert rule.evaluate(in_hour, {"AAA": bars("AAA", in_hour, latest=3_000_000.0)}, 5)
    assert rule.evaluate(out_of_hour, {"AAA": bars("AAA", out_of_hour)}, 5) == []


# ── the control ──────────────────────────────────────────────────────────────
def _universe(as_of: datetime = AT_THE_HOUR) -> dict[str, list[Bar]]:
    return {f"{i:06d}": bars(f"{i:06d}", as_of) for i in range(30)}


def test_the_control_is_reproducible_under_a_fixed_seed():
    """A control whose result moves between runs cannot settle an argument, because a
    disappointing comparison can always be re-drawn."""
    first = RandomControl(seed=7).evaluate(AT_THE_HOUR, _universe(), max_signals=5)
    second = RandomControl(seed=7).evaluate(AT_THE_HOUR, _universe(), max_signals=5)
    assert [(s.symbol, s.rank) for s in first] == [(s.symbol, s.rank) for s in second]


def test_the_control_actually_varies_with_the_seed_and_the_instant():
    """Reproducible must not have quietly become constant — that would make the null
    one fixed basket rather than a random draw."""
    base = RandomControl(seed=7).evaluate(AT_THE_HOUR, _universe(), max_signals=5)
    other_seed = RandomControl(seed=8).evaluate(AT_THE_HOUR, _universe(), max_signals=5)
    later = AT_THE_HOUR + timedelta(days=1)
    other_day = RandomControl(seed=7).evaluate(later, _universe(later), max_signals=5)
    assert [s.symbol for s in base] != [s.symbol for s in other_seed]
    assert [s.symbol for s in base] != [s.symbol for s in other_day]


def test_the_control_draws_the_same_names_however_the_instant_is_spelled():
    """The same moment in UTC and in exchange-local terms must draw identically, or a
    backtest and a live run disagree for a reason nobody would look for."""
    as_utc = AT_THE_HOUR.astimezone(timezone.utc)
    local = RandomControl(seed=7).evaluate(AT_THE_HOUR, _universe(), max_signals=5)
    utc = RandomControl(seed=7).evaluate(as_utc, _universe(as_utc), max_signals=5)
    assert [s.symbol for s in local] == [s.symbol for s in utc]


def test_the_control_emits_the_same_shape_as_a_real_strategy():
    """Same ranks, same stamps, same cap — so both run through one pipeline and any
    difference in the results is attributable to selection rather than to plumbing."""
    signals = RandomControl(seed=7).evaluate(AT_THE_HOUR, _universe(), max_signals=4)
    assert [s.rank for s in signals] == [1, 2, 3, 4]
    assert all(s.timestamp == AT_THE_HOUR for s in signals)
    assert len({s.symbol for s in signals}) == 4


def test_the_control_never_draws_a_name_it_has_no_bars_for():
    """Including one would let the control 'select' instruments the strategy could
    never have seen, which flatters or damns it for the wrong reason."""
    universe = dict(_universe())
    universe["EMPTY"] = []
    signals = RandomControl(seed=7).evaluate(AT_THE_HOUR, universe, max_signals=30)
    assert "EMPTY" not in {s.symbol for s in signals}
    assert len(signals) == 30
