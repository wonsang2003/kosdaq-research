"""The application layer, end to end and offline.

These are the tests that back the claim on the front page: clone this and run it, with
no credentials and no network, and watch a documented finding reproduce itself.
"""

from __future__ import annotations

import pytest

from src.app.config import AppConfig
from src.app.use_cases.run_backtest import run_backtest
from src.app.use_cases.run_session import run_session
from src.app.wiring import build
from src.features.execution.domain.repositories.broker import BrokerUnavailable
from src.features.operations.domain.entities.trading_mode import TradingMode
from src.features.strategy.infrastructure.registry import available_keys, get_strategy


@pytest.fixture
def config(tmp_path) -> AppConfig:
    """Isolated runtime state. Never the repo's own data/runtime — a test that writes
    there could change what the next `make run` reports."""
    return AppConfig(data_dir=tmp_path / "runtime")


def test_the_whole_pipeline_runs_with_no_credentials(config):
    """Collect, store, evaluate, order, fill, journal, report. Offline."""
    system = build(config)
    report = run_session(system)
    assert report.evaluations > 0
    assert report.session_day


def test_the_shipped_default_loses_and_the_runtime_says_so(config):
    """The point of shipping a refuted strategy.

    A reviewer runs one command and the repository's own thesis demonstrates itself:
    a rule selected on a training fold, judged on the session count, loses.
    """
    system = build(config)
    report = run_backtest(system)
    assert report.sessions >= 5, "needs enough sessions to be judgeable at all"
    assert report.day_clustered_t is not None
    assert report.day_clustered_t < 0, "the refuted rule is expected to lose"
    assert "LOSES" in report.verdict_line or "NOISE" in report.verdict_line
    assert "postmortem" in report.verdict_line.lower() or "noise" in report.verdict_line.lower()


def test_judgement_uses_the_session_count_not_the_trade_count(config):
    """The correction that reversed the most results in this project.

    A gap-down cell read t = 12.7 per event and t = -0.92 once clustered on sessions,
    because 1,220 events lived on 304 days with one carrying 62 of them.
    """
    system = build(config)
    report = run_backtest(system)
    assert report.sessions < report.total_fills, "fixture should have more fills than days"
    assert "effective sample size" in report.summary()


def test_a_tiny_sample_is_reported_as_not_judgeable_rather_than_scored(config):
    """'We could not measure' is a result the caller needs, not an exception to catch —
    and definitely not a t-statistic computed on three days."""
    system = build(config)
    report = run_backtest(system, max_sessions=2)
    assert "NOT JUDGEABLE" in report.verdict_line


def test_drop_best_session_is_reported(config):
    """Screening, not a post-hoc excuse. A result carried by one lucky day has to show."""
    system = build(config)
    report = run_backtest(system)
    assert report.without_best_session_t is not None
    assert report.best_session


def test_net_is_reported_after_friction_never_gross_only(config):
    system = build(config)
    report = run_session(system)
    assert report.net_return_pct <= report.gross_return_pct
    assert "after 0.38% round trip" in report.summary()


def test_positions_that_could_not_be_liquidated_are_surfaced(config):
    """Getting in and not being able to get out is this project's most replicated
    finding. A runner that silently completed the exit on paper would reproduce the
    exact fill fantasy the graveyard is full of."""
    system = build(config)
    report = run_session(system)
    assert "stranded" in report.summary()


# ── the alpha boundary ───────────────────────────────────────────────────────
def test_no_registered_strategy_claims_to_work(config):
    """The disclosure policy as an assertion rather than a promise.

    This fails the moment anyone registers a real strategy, which is the point.
    """
    for key in available_keys():
        spec = get_strategy(key).spec
        assert spec.verdict in {"REFUTED", "CONTROL"}, f"{key} declares {spec.verdict}"
        assert spec.is_expected_to_lose


def test_the_banner_warns_before_anything_runs(config):
    """A reviewer is told what they are executing by the program, not by a document
    they have to go and find."""
    system = build(config)
    banner = system.banner()
    assert "REFUTED" in banner
    assert "not working" in banner
    assert "real orders: False" in banner


def test_the_broker_does_not_claim_it_can_move_money(config):
    """The banner depends on this being honest, so it is checked rather than trusted."""
    system = build(config)
    assert system.broker.places_real_orders is False


def test_default_mode_is_dry(config):
    assert build(config).mode is TradingMode.DRY


# ── state recovery ───────────────────────────────────────────────────────────
def test_a_restart_rebuilds_orders_and_positions_from_the_journal(config):
    """The requirement the imitated system did not meet.

    It journalled every order and every fill and read neither back; positions lived in
    a dict that a restart emptied. Here the second `build()` is a restart.
    """
    first = build(config)
    run_session(first)
    before = {s: p.quantity for s, p in first.broker.positions().items()}
    orders_before = len(first.broker.all_orders())
    assert orders_before > 0, "fixture must produce orders for this to mean anything"

    restarted = build(config)          # <- the restart
    assert restarted.restored_orders == orders_before
    assert {s: p.quantity for s, p in restarted.broker.positions().items()} == before


def test_a_fresh_install_starts_empty_without_complaining(config):
    system = build(config)
    assert system.restored_orders == 0
    assert system.restored_fills == 0


def test_state_is_written_under_the_configured_directory_only(config, tmp_path):
    """A run must not touch the repository's shipped evidence."""
    system = build(config)
    run_session(system)
    assert system.config.journal_path.exists()
    assert str(system.config.journal_path).startswith(str(tmp_path))


# ── operator controls reach the running system ───────────────────────────────
def test_the_kill_switch_stops_new_orders(config):
    from src.features.operations.infrastructure.controls import KillSwitchEngaged

    system = build(config)
    system.kill_switch.engage("halt for the test")
    with pytest.raises(KillSwitchEngaged):
        run_session(system)


def test_the_backtest_does_not_write_to_the_live_journal(config):
    """A research run that appended to the live order journal would corrupt the one
    file the running system rebuilds its positions from."""
    system = build(config)
    run_backtest(system, max_sessions=3)
    assert system.journal.replay() == ([], [])
