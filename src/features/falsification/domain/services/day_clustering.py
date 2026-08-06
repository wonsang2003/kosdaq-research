"""Day-clustered significance.

The most consequential statistical correction in this repository, and the one that
reversed the most conclusions. Event studies on KRX produce heavily clustered
samples: a single panic morning generated 62 signals, and one gap-down cell had
1,220 events spread over only 304 sessions. Treating events as independent inflates
the sample by the clustering factor.

Measured consequences, each a conclusion that flipped:

  * a gap-down cell at per-event t = 12.7 became day-clustered t = -0.92
  * a panic x mid-cap cell at per-trade t = 7.4 became t = 0.4
  * a verification script using per-trade t reported '2026 decay, negative';
    the day-clustered figure was +2.09, positive

The rule that came out of it: every event study reports the day-clustered
statistic, and the effective sample size is the session count, not the event count.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Hashable, Iterable, Sequence

from src.features.falsification.domain.entities.clustered_stat import ClusteredStat

from src.shared.domain.errors import InsufficientData



def cluster_by_session(
    values: Iterable[float],
    sessions: Iterable[Hashable],
) -> ClusteredStat:
    """Cluster `values` on `sessions` and compute the session-level t-statistic.

    :param values: per-event outcomes, typically net return in percent.
    :param sessions: session key for each value, aligned positionally. Any hashable
        works; trading-day strings are conventional here.
    :return: the clustered statistic, whose `n_sessions` is the effective sample.
    :raise InsufficientData: if fewer than two sessions are present. Two is the
        minimum at which dispersion is defined; returning a t of 0 or None for a
        single session would let a one-day result enter a comparison table as if it
        had been tested.
    """
    grouped: dict[Hashable, list[float]] = defaultdict(list)
    total = 0
    for value, session in zip(values, sessions, strict=True):
        grouped[session].append(float(value))
        total += 1

    if len(grouped) < 2:
        raise InsufficientData(
            f"day-clustered t needs >= 2 sessions, got {len(grouped)} "
            f"({total} observations). A single session cannot be judged."
        )

    session_means = [statistics.mean(v) for v in grouped.values()]
    mean = statistics.mean(session_means)
    n = len(session_means)
    sd = statistics.pstdev(session_means) * ((n / (n - 1)) ** 0.5)
    t = mean / (sd / n**0.5) if sd > 0 else None

    return ClusteredStat(
        mean=mean,
        t=t,
        n_observations=total,
        n_sessions=n,
        positive_sessions=sum(1 for m in session_means if m > 0),
    )


def drop_worst_session(
    values: Sequence[float],
    sessions: Sequence[Hashable],
) -> tuple[Hashable, ClusteredStat]:
    """Recompute after removing the single best-performing session.

    A screening step, not a post-hoc excuse. Applied at *screening* time it kills
    candidates carried by one lucky day, which is where most of them were dying:
    one entry-time cut fell from day-t 1.83 to 1.40 once its best session was
    removed, and a separate strategy had 74.5% of all profit in its top 20 days.

    :return: (removed session key, statistic over the remainder).
    :raise InsufficientData: if fewer than three sessions are present, since
        dropping one must still leave a computable statistic.
    """
    grouped: dict[Hashable, list[float]] = defaultdict(list)
    for value, session in zip(values, sessions, strict=True):
        grouped[session].append(float(value))

    if len(grouped) < 3:
        raise InsufficientData(
            f"drop-worst-session needs >= 3 sessions, got {len(grouped)}"
        )

    best = max(grouped, key=lambda k: statistics.mean(grouped[k]))
    kept_values: list[float] = []
    kept_sessions: list[Hashable] = []
    for session, vals in grouped.items():
        if session == best:
            continue
        kept_values.extend(vals)
        kept_sessions.extend([session] * len(vals))

    return best, cluster_by_session(kept_values, kept_sessions)
