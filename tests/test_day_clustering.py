"""Day-clustered significance, and the reversals it produced.

Each test here reproduces the shape of a real incident where the per-event
statistic and the day-clustered statistic disagreed about the sign or the
significance of a result. In every one of those incidents the day-clustered figure
was the correct one.
"""

from __future__ import annotations

import pytest

from src.features.falsification.domain.services.day_clustering import (
    cluster_by_session,
    drop_worst_session,
)
from src.shared.domain.errors import InsufficientData


def test_clustering_collapses_a_single_dominant_session():
    """Per-event t = large, day-clustered t = not significant.

    Shape of the gap-down cell that read per-event t = 12.7 and day-clustered
    t = -0.92: 1,220 events over 304 sessions, with one session carrying 62 of
    them. Here one session supplies 60 winning events and nine sessions each
    supply one small loss. Counting events, the winners dominate; counting
    sessions, they are one observation out of ten.
    """
    values = [5.0] * 60 + [-0.5] * 9
    sessions = ["D0"] * 60 + [f"D{i}" for i in range(1, 10)]

    stat = cluster_by_session(values, sessions)

    assert stat.n_observations == 69
    assert stat.n_sessions == 10, "effective sample is sessions, not events"
    assert stat.positive_sessions == 1
    # Event-weighted mean is strongly positive; session-weighted mean is not.
    event_mean = sum(values) / len(values)
    assert event_mean > 4.0
    assert stat.mean == pytest.approx(0.05, abs=1e-9)
    assert stat.t is not None and abs(stat.t) < 2.0


def test_effective_sample_is_reported_not_hidden():
    """n_sessions must be visible next to any t. Reporting n_observations overstates it."""
    stat = cluster_by_session([1.0, 2.0, 3.0, 4.0], ["A", "A", "B", "B"])
    assert (stat.n_observations, stat.n_sessions) == (4, 2)
    assert "over 2 sessions" in str(stat)


def test_single_session_raises_rather_than_returning_zero():
    """A one-day result must not be able to enter a comparison table.

    A single session can produce dozens of fills and a tidy-looking average. That is
    still one observation. Returning t = 0.0 or None would let it sit in a table
    beside a twelve-session figure as though the two met the same standard.
    """
    with pytest.raises(InsufficientData, match="single session cannot be judged"):
        cluster_by_session([1.0] * 96, ["S1"] * 96)


def test_zero_dispersion_gives_none_not_infinity():
    """Identical session means are a degenerate sample, not infinite confidence."""
    stat = cluster_by_session([1.0, 1.0, 1.0], ["A", "B", "C"])
    assert stat.t is None


def test_drop_worst_session_removes_the_best_day():
    """Screening step: a candidate carried by one lucky session must show it.

    Reproduces the entry-time cut that read day-t 1.83 and fell to 1.40 once its
    single best session was removed.
    """
    values = [10.0, 0.1, 0.1, 0.1, 0.1]
    sessions = ["BIG", "A", "B", "C", "D"]

    removed, stat = drop_worst_session(values, sessions)

    assert removed == "BIG"
    assert stat.n_sessions == 4
    assert stat.mean == pytest.approx(0.1)


def test_drop_worst_session_needs_three_sessions():
    with pytest.raises(InsufficientData):
        drop_worst_session([1.0, 2.0], ["A", "B"])


def test_mismatched_lengths_raise():
    """zip(strict=True): a silently truncated pairing would misalign every outcome."""
    with pytest.raises(ValueError):
        cluster_by_session([1.0, 2.0, 3.0], ["A", "B"])
