"""Session-clustered statistic — the effective-sample-size carrier."""

from __future__ import annotations

from pydantic import BaseModel


class ClusteredStat(BaseModel):
    """Result of clustering observations by session."""

    mean: float
    """Mean of the per-session means — not the mean of all observations. These
    differ whenever sessions carry unequal event counts, and the per-session mean
    is the one that matches the t-statistic below."""

    t: float | None
    """t-statistic over per-session means. None when the session-level dispersion is
    zero, which is a degenerate sample rather than infinite confidence."""

    n_observations: int

    n_sessions: int
    """The effective sample size. This is the number to report beside any
    significance claim; `n_observations` overstates it by the clustering factor."""

    positive_sessions: int

    @property
    def positive_session_rate(self) -> float:
        return self.positive_sessions / self.n_sessions if self.n_sessions else 0.0

    def __str__(self) -> str:
        t = f"{self.t:.2f}" if self.t is not None else "n/a"
        return (f"{self.mean:+.3f} (day-t {t}, n={self.n_observations} "
                f"over {self.n_sessions} sessions, {self.positive_sessions} positive)")
