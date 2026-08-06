"""Equivalence with the production trading system.

This repository does not modify the live system. It *copies* research logic out of
a 4,674-line operational file that is running real money, so the two now have
independent copies of the same functions, and copies drift.

These tests pin the drift. Each one imports the original from `app_ec2.py` and the
port from `src/`, feeds both the same fixture, and asserts identical output. If
either side is edited, the test fails and names which one moved.

Skipped automatically when the production file is not present, so a reviewer with
only this repository still gets a green suite. That skip is itself asserted below
so it cannot silently hide a real failure.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from src.features.falsification.domain.services.beta_separation import (
    KILL_BETA,
    SIGN_FLIP_BETA,
    excess_return,
    index_return,
)

PRODUCTION_CANDIDATES = (
    Path.home() / "kosdaq_paper" / "app_ec2.py",
    Path.home() / "kosdaq_paper" / "app.py",
)


def _production_path() -> Path | None:
    return next((p for p in PRODUCTION_CANDIDATES if p.exists()), None)


@pytest.fixture(scope="module")
def production():
    """The live module, imported by path. It is import-safe: server and scheduler
    are behind a __main__ guard."""
    path = _production_path()
    if path is None:
        pytest.skip("production app not present — equivalence not checkable here")
    spec = importlib.util.spec_from_file_location("production_app", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# SYNTHETIC index levels on fictional dates.
#
# An earlier version of this fixture carried the real closes for the actual week the
# beta-separation result was measured on. That is a disclosure: index levels are a
# join key. Combined with the sign and magnitude of the strategy's return over the
# same window, and with public KRX moves and filings for those dates, a reader can
# narrow down positions that were actually held.
#
# Nothing about the behaviour under test needs real data. What matters is the shape:
# three observations, a weekend gap between two of them so the previous-trading-day
# fallback is exercised, and a large enough drawdown to make the excess arithmetic
# meaningful. 2010-06-12 and 2010-06-13 are a Saturday and a Sunday.
INDEX_MAP = {
    "KOSDAQ": {"20100607": 700.00, "20100610": 714.00, "20100614": 600.00},
    "KOSPI": {"20100607": 2000.00, "20100610": 2020.00, "20100614": 1660.00},
}

CASES = [
    ("KOSDAQ", "2010-06-07", "2010-06-14"),
    ("KOSPI", "2010-06-07", "2010-06-14"),
    ("KOSPI", "2010-06-10", "2010-06-14"),
    ("KOSDAQ", "2010-06-10", "2010-06-14"),
    ("KOSDAQ", "2010-06-12", "2010-06-14"),   # Saturday -> falls back to 06-10
    ("KOSDAQ", "2010-06-13", "2010-06-14"),   # Sunday   -> falls back to 06-10
    ("KOSDAQ", "20100607", "20100614"),       # compact form
    ("kospi", "2010-06-07", "2010-06-14"),    # lower case routes to KOSPI
    (None, "2010-06-07", "2010-06-14"),       # None defaults to KOSDAQ
    ("KOSDAQ", "2010-06-07", "2010-06-07"),   # zero-length holding period
    ("KOSDAQ", "2010-05-01", "2010-06-14"),   # start before any known level
    ("KOSDAQ", "", ""),                       # empty dates
    ("KOSDAQ", None, None),
]


@pytest.mark.parametrize(("market", "start", "end"), CASES)
def test_index_return_matches_production(production, market, start, end):
    """The port must reproduce `_idx_ret` exactly, including its None semantics.

    The previous-trading-day fallback is where a reimplementation would silently
    diverge: holding periods start and end on weekends routinely, and a port that
    returned None there instead of falling back would quietly drop those trades
    from the excess calculation rather than failing.
    """
    assert index_return(INDEX_MAP, market, start, end) == production._idx_ret(
        INDEX_MAP, market, start, end
    )


@pytest.mark.parametrize(
    "index_map",
    [None, {}, {"KOSDAQ": {}}, {"KOSPI": {}}, {"NOTABOARD": {"20100607": 1.0}}],
)
def test_missing_index_returns_none_in_both(production, index_map):
    """Absence must propagate as None on both sides.

    A zero here would make excess equal raw and disable beta separation entirely,
    at exactly the moment the index feed is failing — which is when it is most
    needed.
    """
    ours = index_return(index_map, "KOSDAQ", "2010-06-07", "2010-06-14")
    theirs = production._idx_ret(index_map, "KOSDAQ", "2010-06-07", "2010-06-14")
    assert ours is None and theirs is None


def test_kill_beta_matches_production(production):
    """Display beta and kill beta must be the same number in both systems."""
    assert production.KILL_BETA_VIEW == KILL_BETA


def test_kill_beta_is_conservative(production):
    """Beta must be positive and no greater than the measured exposure of the basket.

    A beta *above* the measured value inflates the excess and makes the 'blame the
    market' veto too easy to invoke. The bound is read from production so that the two
    systems cannot drift apart on it.
    """
    import inspect

    source = inspect.getsource(production.ops_alerts)
    bound = float([ln for ln in source.splitlines() if "KILL_BETA=" in ln][0].split("=")[1].split()[0])
    assert 0 < KILL_BETA <= bound or KILL_BETA == bound


def test_negative_raw_inside_a_worse_market_gives_positive_excess():
    """The shape of the incident, on synthetic numbers.

    A strategy is down slightly; the market over the same holding period is down far
    more. Raw says stop, excess says the market did it. The claim being tested is not
    the magnitude — it is that the *sign* of the excess is robust to the choice of
    beta, which is what makes the conclusion defensible without winning an argument
    about the right beta.
    """
    market = index_return(INDEX_MAP, "KOSPI", "2010-06-07", "2010-06-14")
    assert market is not None and market < -15

    raw = -0.50
    assert excess_return(raw, market) > 0, "excess must be positive at the kill beta"

    # Positive for any beta above the flip point — that is the actual claim.
    assert excess_return(raw, market, beta=SIGN_FLIP_BETA + 0.001) > 0
    assert excess_return(raw, market, beta=KILL_BETA * 2) > excess_return(raw, market)


def test_production_file_is_reachable_in_this_environment():
    """Guard against a silent skip.

    If the production path moves, every equivalence test above turns into a skip
    and the suite still reports green. This test fails instead, so the loss of
    coverage is visible. It is the only test here allowed to depend on the
    developer's machine, and it opts out when explicitly told to.
    """
    if os.environ.get("ALLOW_MISSING_PRODUCTION") == "1":
        pytest.skip("explicitly permitted to run without the production system")
    assert _production_path() is not None, (
        "production app not found; equivalence tests are silently skipping. "
        "Set ALLOW_MISSING_PRODUCTION=1 if this is a clean-clone review."
    )
