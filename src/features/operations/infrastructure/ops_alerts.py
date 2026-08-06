"""Structural health checks: the failures logs cannot show.

Every check here answers the same question, which is the question logs are worst at:
*did the thing that was supposed to happen actually happen?* A log shows what ran. It
does not show that the exit job ran, threw nothing, and closed nothing — that failure
has no log line at all, because from the job's own point of view it completed. The
only evidence is the shape of the state left behind: an entry queued four days ago
that is still queued, a position two days past the exit day its own plan set.

That is why these are computed over *state* rather than over log text, and why the
module contains no strategy logic. Nothing here knows what a good trade is. It knows
that a queue should drain, that a registered job should produce outcomes, that the
running code should be the code on disk, and that a data source which cannot answer
is not the same as one that answers no.

The `status` field is the anti-fatigue mechanism described on `AlertStatus`: a finding
an operator has triaged as intentional or already-fixed still appears, but sorts below
everything open, so the top of the dashboard stays the part that needs a human.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime

from src.features.operations.domain.entities.ops_state import (
    PendingItem,
    TrackedPosition,
)
from src.features.operations.domain.entities.scheduled_job import (
    AlertSeverity,
    AlertStatus,
    HealthReport,
    JobOutcome,
    JobResult,
    OpsAlert,
)
from src.shared.domain import clock

# `PendingItem` and `TrackedPosition` are imported for use *and* re-exported, because
# callers assembling the inputs to `build_alerts` reach for them alongside it. They
# are defined in `domain/entities/ops_state.py`: what a stale queue entry or an
# overdue position *is* does not depend on where the state was read from.


def build_alerts(
    *,
    pending: Sequence[PendingItem] = (),
    positions: Sequence[TrackedPosition] = (),
    outcomes: Sequence[JobOutcome] = (),
    job_keys: Sequence[str] = (),
    health: HealthReport | None = None,
    now: datetime | None = None,
    acknowledged: Mapping[str, AlertStatus] | None = None,
    pending_max_age_days: int = 2,
    silence_days: int = 3,
    undecidable_window: int = 5,
    undecidable_ratio: float = 0.5,
) -> list[OpsAlert]:
    """Run every structural check and return the findings, worst first.

    :param job_keys: the registered table (`Scheduler.job_keys`). Silence can only be
        detected against the list of jobs that *should* be producing outcomes; without
        it a job that has never run once is indistinguishable from a job that does not
        exist.
    :param acknowledged: `category` or `category:subject` → status, so a triaged
        finding sorts below open ones instead of being suppressed. Suppression loses
        the evidence that the accepted condition is still true.
    :param now: defaults to `clock.now()`. Exchange-local, like every other timestamp.
    :return: alerts sorted by `OpsAlert.sort_key` — open first, then severity.
    :raise ValueError: on a non-positive window or a ratio outside (0, 1]. A zero
        window would make the undecidable check vacuously silent, which reads on the
        dashboard as "healthy".
    """
    if pending_max_age_days < 0:
        raise ValueError("pending_max_age_days must not be negative")
    if silence_days <= 0:
        raise ValueError("silence_days must be positive")
    if undecidable_window <= 0:
        raise ValueError("undecidable_window must be positive")
    if not 0 < undecidable_ratio <= 1:
        raise ValueError("undecidable_ratio must be in (0, 1]")

    moment = now or clock.now()
    today = moment.astimezone(clock.MARKET_TZ).date()
    ack = dict(acknowledged or {})

    found: list[OpsAlert] = []
    found += _stuck_queue(pending, today, pending_max_age_days, ack)
    found += _overdue_positions(positions, today, ack)
    found += _job_silence(job_keys, outcomes, moment, silence_days, ack)
    found += _undecidable_sources(
        job_keys, outcomes, undecidable_window, undecidable_ratio, ack
    )
    found += _staleness(health, ack)
    return sorted(found, key=lambda alert: alert.sort_key)


# ── checks ───────────────────────────────────────────────────────────────────
def _stuck_queue(
    pending: Sequence[PendingItem],
    today: date,
    max_age_days: int,
    ack: Mapping[str, AlertStatus],
) -> list[OpsAlert]:
    """Items that entered a queue and never left it.

    The job that should have drained them ran and reported success — draining zero
    items is indistinguishable from draining the right number, from inside the job.
    Only the queue's own age shows it.
    """
    aged = sorted(
        (item for item in pending if item.age_days(today) > max_age_days),
        key=lambda item: (-item.age_days(today), item.item_id),
    )
    if not aged:
        return []
    oldest = aged[0].age_days(today)
    severity = (
        AlertSeverity.CRITICAL if oldest > max_age_days * 2 else AlertSeverity.MAJOR
    )
    return [
        OpsAlert(
            severity=severity,
            status=_status("stuck_queue", "", ack),
            category="stuck_queue",
            what=f"{len(aged)} queued item(s) older than {max_age_days}d",
            detail="oldest {}d: {}".format(
                oldest, ", ".join(f"{i.item_id}({i.symbol or i.kind})" for i in aged[:5])
            ),
            count=len(aged),
            action=(
                "the drain job is reporting success while doing nothing — replay it "
                "with run_once and compare the item count it claims against this one"
            ),
        )
    ]


def _overdue_positions(
    positions: Sequence[TrackedPosition],
    today: date,
    ack: Mapping[str, AlertStatus],
) -> list[OpsAlert]:
    """Positions still held past the exit day their own plan set.

    Always CRITICAL: this is money at risk on a schedule nobody chose, and every day
    it persists is an unhedged hold that no backtest ever measured.
    """
    overdue = sorted(
        (
            position
            for position in positions
            if position.quantity != 0 and position.days_overdue(today) > 0
        ),
        key=lambda p: (-p.days_overdue(today), p.symbol),
    )
    return [
        OpsAlert(
            severity=AlertSeverity.CRITICAL,
            status=_status("overdue_position", position.symbol, ack),
            category="overdue_position",
            what=f"{position.symbol} held {position.days_overdue(today)}d past its exit day",
            detail=(
                f"qty={position.quantity} entered={position.entered_on.isoformat()} "
                f"exit_due={position.exit_on.isoformat()}"
            ),
            count=position.days_overdue(today),
            action=(
                "close it by hand, then check whether the exit job produced an "
                "outcome that day — a missing outcome means it never ran"
            ),
        )
        for position in overdue
    ]


def _job_silence(
    job_keys: Sequence[str],
    outcomes: Sequence[JobOutcome],
    moment: datetime,
    silence_days: int,
    ack: Mapping[str, AlertStatus],
) -> list[OpsAlert]:
    """Registered jobs that stopped producing outcomes, and jobs that never succeed.

    Split into two findings on purpose. No outcome at all means the job is not
    running — a crashed loop, a key that never matches, a table entry that was edited
    out. Outcomes with no OK among them means it runs and declines to act every time,
    which the imitated system had for weeks and read as calm.
    """
    cutoff = moment.timestamp() - silence_days * 86400
    alerts: list[OpsAlert] = []
    for key in job_keys:
        mine = [o for o in outcomes if o.key == key]
        recent = [o for o in mine if o.finished_at.timestamp() >= cutoff]
        if not recent:
            last = max((o.finished_at for o in mine), default=None)
            alerts.append(
                OpsAlert(
                    severity=AlertSeverity.MAJOR,
                    status=_status("job_silence", key, ack),
                    category="job_silence",
                    what=f"job {key!r} produced no outcome in {silence_days}d",
                    detail=(
                        f"last outcome {last.isoformat()}"
                        if last is not None
                        else "no outcome has ever been recorded"
                    ),
                    count=silence_days,
                    action=(
                        f"run `run_once({key!r})` — if it works by hand the loop is "
                        "not reaching it, so check the fire time and the persisted "
                        "last-fired map"
                    ),
                )
            )
            continue
        if not any(o.result is JobResult.OK for o in recent):
            alerts.append(
                OpsAlert(
                    severity=AlertSeverity.MINOR,
                    status=_status("job_never_succeeds", key, ack),
                    category="job_never_succeeds",
                    what=f"job {key!r} ran {len(recent)}x in {silence_days}d, never OK",
                    detail="results: "
                    + ", ".join(sorted({o.result.value for o in recent})),
                    count=len(recent),
                    action=(
                        "a job that only ever skips is either correctly idle or "
                        "silently gated off — read the skip reason before assuming "
                        "the first"
                    ),
                )
            )
    return alerts


def _undecidable_sources(
    job_keys: Sequence[str],
    outcomes: Sequence[JobOutcome],
    window: int,
    ratio: float,
    ack: Mapping[str, AlertStatus],
) -> list[OpsAlert]:
    """Jobs whose recent runs keep answering "cannot tell".

    UNDECIDABLE is the correct answer when a source is down, and returning it is a
    success of the design — the job refused to guess. Repeating it is a broken data
    source, and the reason it needs its own alert is that every individual run looks
    handled: nothing raised, nothing was logged as an error, and the job reported a
    valid result. Only the rate gives it away.
    """
    alerts: list[OpsAlert] = []
    for key in job_keys:
        recent = [o for o in outcomes if o.key == key][-window:]
        if len(recent) < window:
            continue  # too few runs to distinguish a bad source from a bad day
        undecided = [o for o in recent if o.result is JobResult.UNDECIDABLE]
        if len(undecided) / len(recent) < ratio:
            continue
        alerts.append(
            OpsAlert(
                severity=AlertSeverity.MAJOR,
                status=_status("undecidable_rate", key, ack),
                category="undecidable_rate",
                what=(
                    f"job {key!r} returned UNDECIDABLE {len(undecided)} of its last "
                    f"{len(recent)} runs"
                ),
                detail="; ".join(o.detail for o in undecided if o.detail)[:400],
                count=len(undecided),
                action=(
                    "the job is refusing to guess, which is right — fix the source it "
                    "cannot read (calendar, feed, reference data) rather than the job"
                ),
            )
        )
    return alerts


def _staleness(
    health: HealthReport | None, ack: Mapping[str, AlertStatus]
) -> list[OpsAlert]:
    """Running code is not the code on disk.

    The incident: a deploy died between upload and restart, `systemctl is-active` said
    healthy — a stale process is still active — and the box ran hours-old logic
    against a table that had been changed on disk.
    """
    if health is None or not health.is_stale:
        return []
    return [
        OpsAlert(
            severity=AlertSeverity.CRITICAL,
            status=_status("stale_build", "", ack),
            category="stale_build",
            what="running build does not match the code on disk",
            detail=f"loaded={health.build_sha[:12]} disk={health.disk_sha[:12]}",
            count=1,
            action=(
                "restart the service; `is-active` cannot detect this because a stale "
                "process is still active"
            ),
        )
    ]


def _status(
    category: str, subject: str, ack: Mapping[str, AlertStatus]
) -> AlertStatus:
    """Resolve triage state, most specific acknowledgement first.

    Defaults to OPEN. An unrecognised acknowledgement key does nothing — a typo in the
    triage file must not silence a real alert.
    """
    if subject and f"{category}:{subject}" in ack:
        return ack[f"{category}:{subject}"]
    return ack.get(category, AlertStatus.OPEN)
