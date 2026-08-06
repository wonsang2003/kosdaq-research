"""Structured logging and structural alerting.

The alert tests are the important half. Each one describes a failure that produced no
log line at all — the job ran, threw nothing, and did nothing — which is why the
checks read state rather than text.
"""

from __future__ import annotations

import io
import os
from datetime import date, datetime, timedelta

import pytest

from src.features.operations.domain.entities.scheduled_job import (
    AlertSeverity,
    AlertStatus,
    HealthReport,
    JobOutcome,
    JobResult,
)
from src.features.operations.infrastructure.logging import (
    LOG_EVENT_KIND,
    LogLevel,
    StructuredLogger,
    read_log_events,
)
from src.features.operations.infrastructure.ops_alerts import (
    PendingItem,
    TrackedPosition,
    build_alerts,
)
from src.shared.domain import clock

MONDAY = datetime(2026, 3, 2, 10, 30, tzinfo=clock.MARKET_TZ)
TODAY = MONDAY.date()


def freeze(monkeypatch, moment: datetime = MONDAY) -> None:
    monkeypatch.setattr(clock, "now", lambda: moment)


def outcome(key: str, result: JobResult, when: datetime, detail: str = "") -> JobOutcome:
    return JobOutcome(
        key=key, started_at=when, finished_at=when, result=result, detail=detail
    )


# ── structured logging ───────────────────────────────────────────────────────
def test_timestamps_are_exchange_local_and_aware(tmp_path, monkeypatch):
    """The imitated system logged naive UTC while trading Seoul-local. Reconciling the
    two after the fact cost more than the incident being reconciled."""
    freeze(monkeypatch)
    stream = io.StringIO()
    StructuredLogger("scan", stream=stream).info("started")
    assert "2026-03-02T10:30:00+09:00" in stream.getvalue()
    assert "[scan]" in stream.getvalue()


def test_lines_below_the_minimum_level_are_not_printed(tmp_path, monkeypatch):
    freeze(monkeypatch)
    stream = io.StringIO()
    logger = StructuredLogger("scan", stream=stream, min_level=LogLevel.INFO)
    logger.debug("noise")
    logger.info("signal")
    assert "noise" not in stream.getvalue()
    assert "signal" in stream.getvalue()


def test_an_error_line_is_also_appended_to_the_ops_stream(tmp_path, monkeypatch):
    """Stdout went to a box nobody was tailing; the dashboard was the only surface
    anyone opened. A log that is not read is exhaust, not monitoring."""
    freeze(monkeypatch)
    path = tmp_path / "ops.jsonl"
    logger = StructuredLogger("scan", events_path=path, stream=io.StringIO())
    logger.error("order rejected by venue", symbol="005930")

    events = logger.events()
    assert len(events) == 1
    assert events[0]["kind"] == LOG_EVENT_KIND
    assert events[0]["level"] == "ERROR"
    assert events[0]["fields"] == {"symbol": "005930"}
    assert events[0]["day"] == "2026-03-02"


def test_a_pattern_match_is_captured_even_when_the_level_filter_hides_the_line(
    tmp_path, monkeypatch
):
    """`WARNING: rate limited, skipping` under a quietened console is exactly the
    trace an incident needs. Deciding capture on the level alone loses it."""
    freeze(monkeypatch)
    path = tmp_path / "ops.jsonl"
    stream = io.StringIO()
    logger = StructuredLogger(
        "scan", events_path=path, stream=stream, min_level=LogLevel.ERROR
    )
    logger.warning("rate limited by the quote feed, skipping")

    assert stream.getvalue() == ""
    events = logger.events()
    assert len(events) == 1
    assert events[0]["level"] == "WARNING"
    assert "rate" in events[0]["matched"]


def test_an_ordinary_line_is_not_captured(tmp_path, monkeypatch):
    """Over-capture is the cheap direction, but capturing everything makes the stream
    another thing nobody reads."""
    freeze(monkeypatch)
    path = tmp_path / "ops.jsonl"
    logger = StructuredLogger("scan", events_path=path, stream=io.StringIO())
    logger.info("scanned 812 symbols")
    assert logger.events() == []


def test_a_field_value_can_promote_a_line(tmp_path, monkeypatch):
    """The signal is often in the context, not the sentence."""
    freeze(monkeypatch)
    logger = StructuredLogger(
        "scan", events_path=tmp_path / "ops.jsonl", stream=io.StringIO()
    )
    logger.info("quote fetch finished", status="timeout")
    assert len(logger.events()) == 1


def test_the_logging_path_never_raises_when_the_sink_is_unwritable(
    tmp_path, monkeypatch
):
    """A logger that raises turns a diagnostic into an outage, and the caller is
    usually inside an `except` block already."""
    freeze(monkeypatch)
    blocked = tmp_path / "ro"
    blocked.mkdir()
    os.chmod(blocked, 0o500)
    try:
        logger = StructuredLogger(
            "scan",
            events_path=blocked / "ops.jsonl",
            stream=io.StringIO(),
            stderr=io.StringIO(),
        )
        logger.error("everything is on fire")  # must not raise
        assert logger.dropped_events == 1, "a silent drop would make the logger lie"
    finally:
        os.chmod(blocked, 0o700)


def test_a_broken_console_does_not_take_the_process_down(tmp_path, monkeypatch):
    freeze(monkeypatch)

    class Exploding(io.StringIO):
        def write(self, _: str) -> int:
            raise OSError("broken pipe")

    logger = StructuredLogger("scan", stream=Exploding(), stderr=io.StringIO())
    logger.info("hello")
    assert logger.dropped_events == 1


def test_an_unserialisable_field_is_kept_as_text_rather_than_losing_the_record(
    tmp_path, monkeypatch
):
    """Losing an error to a serialisation failure of its own context is the worst
    possible trade."""
    freeze(monkeypatch)

    class Opaque:
        def __repr__(self) -> str:
            return "<Opaque>"

    logger = StructuredLogger(
        "scan", events_path=tmp_path / "ops.jsonl", stream=io.StringIO()
    )
    logger.exception("submit failed", RuntimeError("nope"), context=Opaque())

    event = logger.events()[0]
    assert event["fields"]["context"] == "<Opaque>"
    assert event["fields"]["error_type"] == "RuntimeError"
    assert logger.dropped_events == 0


def test_log_events_and_job_outcomes_can_share_one_stream(tmp_path, monkeypatch):
    """Both writers tag their records, so a reader never has to guess at field names
    to tell a job outcome from a captured log line."""
    from src.features.operations.domain.entities.scheduled_job import ScheduledJob
    from src.features.operations.infrastructure.scheduler import Scheduler, read_outcomes

    freeze(monkeypatch, datetime(2026, 3, 2, 9, 0, tzinfo=clock.MARKET_TZ))
    path = tmp_path / "ops.jsonl"
    scheduler = Scheduler(
        state_path=tmp_path / "s.json", events_path=path, poll_seconds=0.01
    )
    scheduler.register(ScheduledJob(at="0900", key="open_scan", run=lambda: JobResult.OK))
    scheduler.tick()
    assert scheduler.wait_for_idle()
    StructuredLogger("scan", events_path=path, stream=io.StringIO()).error("boom")

    assert len(read_outcomes(path)) == 1
    assert len(read_log_events(path)) == 1


# ── stuck queues ─────────────────────────────────────────────────────────────
def test_an_aged_pending_item_raises_a_stuck_queue_alert(monkeypatch):
    """The failure logs cannot show: the drain job ran, threw nothing, and drained
    nothing. From inside the job, draining zero is indistinguishable from draining
    the right number."""
    alerts = build_alerts(
        pending=[PendingItem(item_id="e-1", symbol="005930", queued_on=TODAY - timedelta(days=4))],
        now=MONDAY,
        pending_max_age_days=2,
    )
    assert [a.category for a in alerts] == ["stuck_queue"]
    assert alerts[0].count == 1
    assert "e-1" in alerts[0].detail
    assert alerts[0].action


def test_a_fresh_pending_item_is_not_an_alert(monkeypatch):
    alerts = build_alerts(
        pending=[PendingItem(item_id="e-1", queued_on=TODAY - timedelta(days=1))],
        now=MONDAY,
        pending_max_age_days=2,
    )
    assert alerts == []


def test_a_very_old_queue_escalates_to_critical(monkeypatch):
    alerts = build_alerts(
        pending=[
            PendingItem(item_id="e-1", queued_on=TODAY - timedelta(days=3)),
            PendingItem(item_id="e-2", queued_on=TODAY - timedelta(days=9)),
        ],
        now=MONDAY,
        pending_max_age_days=2,
    )
    assert alerts[0].severity is AlertSeverity.CRITICAL
    assert alerts[0].count == 2


def test_a_position_past_its_exit_day_is_critical(monkeypatch):
    """Money at risk on a schedule nobody chose. Every extra day is an unhedged hold
    that no backtest ever measured."""
    alerts = build_alerts(
        positions=[
            TrackedPosition(
                symbol="035720",
                quantity=100,
                entered_on=TODAY - timedelta(days=5),
                exit_on=TODAY - timedelta(days=2),
            )
        ],
        now=MONDAY,
    )
    assert alerts[0].category == "overdue_position"
    assert alerts[0].severity is AlertSeverity.CRITICAL
    assert alerts[0].count == 2
    assert "035720" in alerts[0].what


def test_a_flat_position_past_its_exit_day_is_not_an_alert(monkeypatch):
    alerts = build_alerts(
        positions=[
            TrackedPosition(
                symbol="035720",
                quantity=0,
                entered_on=TODAY - timedelta(days=5),
                exit_on=TODAY - timedelta(days=2),
            )
        ],
        now=MONDAY,
    )
    assert alerts == []


# ── job silence ──────────────────────────────────────────────────────────────
def test_a_registered_job_with_no_recent_outcome_is_flagged(monkeypatch):
    """No outcome at all means the job is not running: a crashed loop, a fire time
    that never matches, a table entry edited out."""
    alerts = build_alerts(
        job_keys=["open_scan", "eod_report"],
        outcomes=[outcome("open_scan", JobResult.OK, MONDAY - timedelta(hours=1))],
        now=MONDAY,
        silence_days=3,
    )
    assert [a.category for a in alerts] == ["job_silence"]
    assert "eod_report" in alerts[0].what
    assert "no outcome has ever been recorded" in alerts[0].detail


def test_a_job_that_ran_but_never_succeeded_is_a_separate_finding(monkeypatch):
    """It runs and declines to act every time. The imitated system had this for weeks
    and read it as calm."""
    alerts = build_alerts(
        job_keys=["open_scan"],
        outcomes=[
            outcome("open_scan", JobResult.SKIPPED, MONDAY - timedelta(days=d))
            for d in (0, 1, 2)
        ],
        now=MONDAY,
        silence_days=3,
    )
    assert [a.category for a in alerts] == ["job_never_succeeds"]
    assert alerts[0].count == 3


def test_an_undecidable_outcome_does_not_count_as_success(monkeypatch):
    """The whole point of the fourth result state. If UNDECIDABLE satisfied the
    silence check, a job whose data source died would look healthy forever."""
    alerts = build_alerts(
        job_keys=["calendar_gate"],
        outcomes=[
            outcome("calendar_gate", JobResult.UNDECIDABLE, MONDAY - timedelta(days=d))
            for d in (0, 1)
        ],
        now=MONDAY,
        silence_days=3,
    )
    assert "job_never_succeeds" in {a.category for a in alerts}


def test_an_old_success_does_not_stop_the_silence_clock(monkeypatch):
    alerts = build_alerts(
        job_keys=["open_scan"],
        outcomes=[outcome("open_scan", JobResult.OK, MONDAY - timedelta(days=9))],
        now=MONDAY,
        silence_days=3,
    )
    assert [a.category for a in alerts] == ["job_silence"]
    assert "2026-02-21" in alerts[0].detail


# ── undecidable rate ─────────────────────────────────────────────────────────
def test_a_job_that_keeps_answering_undecidable_is_a_broken_data_source(monkeypatch):
    """Every individual run looks handled — nothing raised, nothing logged as an
    error, a valid result returned. Only the rate gives it away."""
    alerts = build_alerts(
        job_keys=["calendar_gate"],
        outcomes=[
            outcome(
                "calendar_gate",
                JobResult.UNDECIDABLE,
                MONDAY - timedelta(hours=h),
                detail="holiday calendar returned no answer",
            )
            for h in range(5)
        ],
        now=MONDAY,
        undecidable_window=5,
        undecidable_ratio=0.5,
    )
    categories = [a.category for a in alerts]
    assert "undecidable_rate" in categories
    flagged = next(a for a in alerts if a.category == "undecidable_rate")
    assert flagged.count == 5
    assert "holiday calendar" in flagged.detail


def test_too_few_runs_is_not_enough_to_accuse_the_source(monkeypatch):
    """One bad day is not a broken feed, and an alert that fires on n=1 trains people
    to ignore it."""
    alerts = build_alerts(
        job_keys=["calendar_gate"],
        outcomes=[
            outcome("calendar_gate", JobResult.UNDECIDABLE, MONDAY - timedelta(hours=1))
        ],
        now=MONDAY,
        undecidable_window=5,
    )
    assert "undecidable_rate" not in {a.category for a in alerts}


def test_a_mostly_healthy_job_is_not_flagged_for_one_undecidable(monkeypatch):
    alerts = build_alerts(
        job_keys=["calendar_gate"],
        outcomes=[
            outcome("calendar_gate", JobResult.OK, MONDAY - timedelta(hours=h))
            for h in range(4)
        ]
        + [outcome("calendar_gate", JobResult.UNDECIDABLE, MONDAY)],
        now=MONDAY,
        undecidable_window=5,
    )
    assert alerts == []


# ── staleness ────────────────────────────────────────────────────────────────
def _health(build_sha: str, disk_sha: str) -> HealthReport:
    return HealthReport(
        build_sha=build_sha,
        disk_sha=disk_sha,
        booted_at=MONDAY - timedelta(hours=2),
        uptime_seconds=7200,
        mode="DRY",
        kill_switch_engaged=False,
    )


def test_running_code_that_differs_from_disk_is_critical(monkeypatch):
    """A deploy died between upload and restart and `is-active` said healthy, because
    a stale process is still active."""
    alerts = build_alerts(now=MONDAY, health=_health("aaaaaaaaaaaa", "bbbbbbbbbbbb"))
    assert alerts[0].category == "stale_build"
    assert alerts[0].severity is AlertSeverity.CRITICAL
    assert "restart" in alerts[0].action


def test_matching_shas_produce_nothing(monkeypatch):
    assert build_alerts(now=MONDAY, health=_health("same", "same")) == []


# ── triage and ordering ──────────────────────────────────────────────────────
def test_alerts_sort_open_first_so_the_top_row_needs_a_human(monkeypatch):
    """Without triage a dashboard accumulates known-and-accepted rows until nobody
    reads it, at which point alerting is worse than none."""
    alerts = build_alerts(
        pending=[PendingItem(item_id="e-1", queued_on=TODAY - timedelta(days=9))],
        job_keys=["eod_report"],
        outcomes=[
            outcome("eod_report", JobResult.SKIPPED, MONDAY - timedelta(hours=1))
        ],
        now=MONDAY,
        acknowledged={"stuck_queue": AlertStatus.DESIGN},
    )
    assert [(a.status, a.severity) for a in alerts] == [
        (AlertStatus.OPEN, AlertSeverity.MINOR),
        (AlertStatus.DESIGN, AlertSeverity.CRITICAL),
    ], "an acknowledged critical must sort below an open minor"


def test_a_triage_key_can_name_one_subject_without_silencing_the_rest(monkeypatch):
    alerts = build_alerts(
        job_keys=["open_scan", "eod_report"],
        now=MONDAY,
        acknowledged={"job_silence:eod_report": AlertStatus.FIXED},
    )
    by_key = {a.what: a.status for a in alerts}
    assert by_key["job 'eod_report' produced no outcome in 3d"] is AlertStatus.FIXED
    assert by_key["job 'open_scan' produced no outcome in 3d"] is AlertStatus.OPEN


def test_an_unknown_triage_key_does_not_silence_a_real_alert(monkeypatch):
    """A typo in the triage file must not be a way to lose an alert."""
    alerts = build_alerts(
        job_keys=["open_scan"],
        now=MONDAY,
        acknowledged={"job_silence:opne_scan": AlertStatus.DESIGN},
    )
    assert alerts[0].status is AlertStatus.OPEN


def test_every_alert_carries_an_action(monkeypatch):
    """An alert without an action is a fact, and facts do not wake anyone up usefully."""
    alerts = build_alerts(
        pending=[PendingItem(item_id="e-1", queued_on=TODAY - timedelta(days=9))],
        positions=[
            TrackedPosition(
                symbol="035720", quantity=10, entered_on=TODAY - timedelta(days=4),
                exit_on=TODAY - timedelta(days=1),
            )
        ],
        job_keys=["open_scan", "calendar_gate"],
        outcomes=[
            outcome("calendar_gate", JobResult.UNDECIDABLE, MONDAY - timedelta(hours=h))
            for h in range(5)
        ],
        health=_health("aaa", "bbb"),
        now=MONDAY,
    )
    assert len(alerts) >= 5
    assert all(alert.action for alert in alerts)
    assert all(alert.status is AlertStatus.OPEN for alert in alerts)


def test_the_default_now_comes_from_the_clock_not_the_host(monkeypatch):
    """Same rule as everywhere else: one time accessor, and it is exchange-local."""
    freeze(monkeypatch)
    alerts = build_alerts(
        pending=[PendingItem(item_id="e-1", queued_on=date(2026, 2, 20))]
    )
    assert alerts[0].category == "stuck_queue"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"silence_days": 0},
        {"undecidable_window": 0},
        {"undecidable_ratio": 0.0},
        {"undecidable_ratio": 1.5},
        {"pending_max_age_days": -1},
    ],
)
def test_a_nonsense_threshold_raises_rather_than_going_quiet(kwargs):
    """A zero window makes the check vacuously silent, and silence reads on a
    dashboard as health."""
    with pytest.raises(ValueError):
        build_alerts(now=MONDAY, **kwargs)
