"""Scheduler behaviour, with each test naming the failure it guards.

The two headline tests are `test_a_duplicate_key_is_refused_at_registration` and
`test_deduplication_survives_a_restart`. Both encode bugs that shipped: one job that
silently never fired, and a restart that re-armed jobs which had already run.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from src.features.operations.domain.entities.scheduled_job import (
    JobOutcome,
    JobResult,
    ScheduledJob,
)
from src.features.operations.infrastructure.atomic_store import StateUnavailable
from src.features.operations.infrastructure.scheduler import (
    DuplicateJobKey,
    Scheduler,
    UnknownJob,
    read_outcomes,
)
from src.shared.domain import clock

# 2026-03-02 is a Monday; 2026-03-07 is a Saturday.
MONDAY_0900 = datetime(2026, 3, 2, 9, 0, tzinfo=clock.MARKET_TZ)
SATURDAY_0900 = datetime(2026, 3, 7, 9, 0, tzinfo=clock.MARKET_TZ)


def build(tmp_path, **kwargs) -> Scheduler:
    """A scheduler on a fixed pair of paths, so a second one can be built over the
    same state to simulate a restart."""
    return Scheduler(
        state_path=tmp_path / "scheduler.json",
        events_path=tmp_path / "ops.jsonl",
        poll_seconds=kwargs.pop("poll_seconds", 0.01),
        **kwargs,
    )


def job(key: str, at: str = "0900", run=None, **kwargs) -> ScheduledJob:
    return ScheduledJob(at=at, key=key, run=run or (lambda: JobResult.OK), **kwargs)


def freeze(monkeypatch, moment: datetime) -> None:
    monkeypatch.setattr(clock, "now", lambda: moment)


# ── registration ─────────────────────────────────────────────────────────────
def test_a_duplicate_key_is_refused_at_registration(tmp_path):
    """The original let two jobs share a key. The second never fired, and nothing
    errored and nothing ran — the symptom was an absence, which is invisible."""
    scheduler = build(tmp_path)
    scheduler.register(job("open_scan", at="0900"))
    with pytest.raises(DuplicateJobKey, match="open_scan"):
        scheduler.register(job("open_scan", at="1530"))


def test_the_registry_keeps_order_and_exposes_a_readable_table(tmp_path):
    """'What does this box do at 15:30' must be answerable by looking."""
    scheduler = build(tmp_path)
    scheduler.register(job("eod_report", at="1600", description="write the day"))
    scheduler.register(job("open_scan", at="0900", description="scan"))
    assert scheduler.job_keys == ("eod_report", "open_scan")
    assert scheduler.table() == (
        ("0900", "open_scan", "scan"),
        ("1600", "eod_report", "write the day"),
    )


# ── firing ───────────────────────────────────────────────────────────────────
def test_a_frozen_clock_drives_firing_deterministically(tmp_path, monkeypatch):
    freeze(monkeypatch, MONDAY_0900)
    scheduler = build(tmp_path)
    ran: list[str] = []
    scheduler.register(job("open_scan", at="0900", run=lambda: (ran.append("x"), JobResult.OK)[1]))
    scheduler.register(job("eod_report", at="1600"))

    assert scheduler.tick() == ["open_scan"]
    assert scheduler.wait_for_idle()
    assert ran == ["x"]


def test_a_job_does_not_fire_outside_its_minute(tmp_path, monkeypatch):
    freeze(monkeypatch, MONDAY_0900.replace(minute=1))
    scheduler = build(tmp_path)
    scheduler.register(job("open_scan", at="0900"))
    assert scheduler.tick() == []


def test_a_job_fires_once_per_day_not_once_per_poll(tmp_path, monkeypatch):
    """Twenty-second polling means the same minute is seen three times."""
    freeze(monkeypatch, MONDAY_0900)
    scheduler = build(tmp_path)
    scheduler.register(job("open_scan", at="0900"))

    assert scheduler.tick() == ["open_scan"]
    assert scheduler.tick() == []
    assert scheduler.tick(MONDAY_0900 + timedelta(seconds=40)) == []
    assert scheduler.wait_for_idle()
    assert len(scheduler.outcomes()) == 1


def test_the_next_calendar_day_re_arms_the_job(tmp_path, monkeypatch):
    freeze(monkeypatch, MONDAY_0900)
    scheduler = build(tmp_path)
    scheduler.register(job("open_scan", at="0900"))
    scheduler.tick()
    assert scheduler.tick(MONDAY_0900 + timedelta(days=1)) == ["open_scan"]


def test_deduplication_survives_a_restart(tmp_path, monkeypatch):
    """The bug this module exists to fix.

    The original held the last-fired map in memory. Restarting at 09:00:10 — which is
    when restarts happen, because 09:00 is when things break — cleared it, and the
    next poll ten seconds later re-fired a job that had already run.
    """
    freeze(monkeypatch, MONDAY_0900)
    first = build(tmp_path)
    first.register(job("open_scan", at="0900"))
    assert first.tick() == ["open_scan"]
    assert first.wait_for_idle()
    del first  # the process dies

    restarted = build(tmp_path)
    restarted.register(job("open_scan", at="0900"))
    assert restarted.tick(MONDAY_0900 + timedelta(seconds=20)) == [], (
        "a restart inside the fire minute must not re-arm an already-fired job"
    )
    assert restarted.fired_days() == {"open_scan": "2026-03-02"}


def test_the_fire_mark_is_persisted_before_the_job_runs(tmp_path, monkeypatch):
    """At-most-once. A process killed mid-run must not re-run the job on restart: a
    re-fired entry job places a second order, whereas a run that never finished shows
    up as silence, which `ops_alerts` raises."""
    freeze(monkeypatch, MONDAY_0900)
    scheduler = build(tmp_path)
    marked: list[dict] = []
    scheduler.register(
        job(
            "open_scan",
            at="0900",
            run=lambda: (marked.append(scheduler.fired_days()), JobResult.OK)[1],
        )
    )
    scheduler.tick()
    assert scheduler.wait_for_idle()
    assert marked == [{"open_scan": "2026-03-02"}]


def test_weekends_are_gated_and_the_gate_is_per_job(tmp_path, monkeypatch):
    freeze(monkeypatch, SATURDAY_0900)
    scheduler = build(tmp_path)
    scheduler.register(job("weekday_only", at="0900"))
    scheduler.register(job("always", at="0900", weekdays_only=False))
    assert scheduler.tick() == ["always"]


def test_the_weekday_gate_is_evaluated_in_market_time(tmp_path, monkeypatch):
    """Saturday 09:00 KST is Friday 00:00 UTC. A host-local weekday check would let
    the job through — which is the same class of bug as the naive session window."""
    friday_utc = datetime(2026, 3, 6, 15, 30, tzinfo=timezone.utc)  # Sat 00:30 KST
    scheduler = build(tmp_path)
    scheduler.register(job("weekday_only", at="0030"))
    assert scheduler.tick(friday_utc) == []


# ── failure isolation ────────────────────────────────────────────────────────
def test_a_throwing_job_does_not_kill_the_loop_and_is_recorded_as_failed(
    tmp_path, monkeypatch
):
    """One job raising must not take the other jobs, or the loop, with it."""
    freeze(monkeypatch, MONDAY_0900)
    scheduler = build(tmp_path)

    def boom() -> JobResult:
        raise RuntimeError("upstream feed exploded")

    scheduler.register(job("bad", at="0900", run=boom))
    scheduler.register(job("good", at="0900"))

    assert scheduler.tick() == ["bad", "good"]
    assert scheduler.wait_for_idle()

    by_key = {o.key: o for o in scheduler.outcomes()}
    assert by_key["bad"].result is JobResult.FAILED
    assert "upstream feed exploded" in by_key["bad"].detail
    assert by_key["good"].result is JobResult.OK

    # and the loop is still able to serve the next day
    assert scheduler.tick(MONDAY_0900 + timedelta(days=1)) == ["bad", "good"]
    assert scheduler.wait_for_idle()


def test_a_job_that_blocks_does_not_block_its_siblings(tmp_path, monkeypatch):
    """Each job gets its own daemon thread. A hung socket in one job must not hold the
    poll loop, which would silently stop every later job in the table."""
    freeze(monkeypatch, MONDAY_0900)
    scheduler = build(tmp_path)
    release = threading.Event()
    finished = threading.Event()

    def hang() -> JobResult:
        release.wait(5)
        return JobResult.OK

    scheduler.register(job("hangs", at="0900", run=hang))
    scheduler.register(job("quick", at="0900", run=lambda: (finished.set(), JobResult.OK)[1]))

    scheduler.tick()
    assert finished.wait(5), "the second job ran while the first was still blocked"
    release.set()
    assert scheduler.wait_for_idle()


def test_a_job_returning_something_other_than_a_jobresult_is_failed(
    tmp_path, monkeypatch
):
    """A slipped contract returns None, and None recorded as OK is a success count
    that means nothing."""
    freeze(monkeypatch, MONDAY_0900)
    scheduler = build(tmp_path)
    scheduler.register(job("sloppy", at="0900", run=lambda: None))
    scheduler.tick()
    assert scheduler.wait_for_idle()
    outcome = scheduler.outcomes()[0]
    assert outcome.result is JobResult.FAILED
    assert "expected a JobResult" in outcome.detail


def test_undecidable_is_recorded_and_is_not_counted_as_success(tmp_path, monkeypatch):
    """'Could not tell' is its own state. Collapsing it into OK or SKIPPED is the
    None-as-False collapse that produced this project's worst silent failures."""
    freeze(monkeypatch, MONDAY_0900)
    scheduler = build(tmp_path)
    scheduler.register(
        job("calendar_gate", at="0900", run=lambda: JobResult.UNDECIDABLE)
    )
    scheduler.tick()
    assert scheduler.wait_for_idle()
    outcome = scheduler.outcomes()[0]
    assert outcome.result is JobResult.UNDECIDABLE
    assert outcome.result is not JobResult.OK
    assert outcome.result is not JobResult.SKIPPED


def test_corrupt_state_raises_rather_than_re_arming_everything(tmp_path, monkeypatch):
    """An unreadable dedup map degraded to `{}` would re-fire every job in the table.
    Raising is the only safe reading of corruption here."""
    freeze(monkeypatch, MONDAY_0900)
    (tmp_path / "scheduler.json").write_text("[]")
    scheduler = build(tmp_path)
    scheduler.register(job("open_scan", at="0900"))
    with pytest.raises(StateUnavailable):
        scheduler.tick()


# ── run_once ─────────────────────────────────────────────────────────────────
def test_run_once_bypasses_the_schedule(tmp_path, monkeypatch):
    """Every job independently runnable from a shell. A job you cannot invoke by hand
    is a job you cannot debug during the incident it caused."""
    freeze(monkeypatch, SATURDAY_0900)  # wrong day and, at 1600, the wrong minute
    scheduler = build(tmp_path)
    scheduler.register(job("eod_report", at="1600"))

    outcome = scheduler.run_once("eod_report")
    assert isinstance(outcome, JobOutcome)
    assert outcome.result is JobResult.OK
    assert [o.key for o in scheduler.outcomes()] == ["eod_report"]


def test_run_once_suppresses_the_scheduled_run_the_same_day(tmp_path, monkeypatch):
    """An operator who ran the order job by hand at 09:05 does not want it running
    again at 09:10."""
    freeze(monkeypatch, MONDAY_0900)
    scheduler = build(tmp_path)
    scheduler.register(job("open_scan", at="0900"))
    scheduler.run_once("open_scan")
    assert scheduler.tick() == []


def test_run_once_can_decline_to_mark_so_a_rehearsal_is_not_the_real_run(
    tmp_path, monkeypatch
):
    freeze(monkeypatch, MONDAY_0900)
    scheduler = build(tmp_path)
    scheduler.register(job("open_scan", at="0900"))
    scheduler.run_once("open_scan", mark_fired=False)
    assert scheduler.tick() == ["open_scan"]
    assert scheduler.wait_for_idle()


def test_run_once_on_an_unknown_key_raises(tmp_path):
    """An operator typing the wrong key at 3am must be told, not left believing a job
    ran."""
    scheduler = build(tmp_path)
    with pytest.raises(UnknownJob, match="typo"):
        scheduler.run_once("typo")


def test_run_once_returns_failure_rather_than_raising_out_of_the_job(
    tmp_path, monkeypatch
):
    """The CLI needs an exit code, not a traceback that loses the journal entry."""
    freeze(monkeypatch, MONDAY_0900)
    scheduler = build(tmp_path)
    scheduler.register(job("bad", at="0900", run=_raise))
    outcome = scheduler.run_once("bad")
    assert outcome.result is JobResult.FAILED
    assert "ValueError" in outcome.detail


def _raise() -> JobResult:
    raise ValueError("no data")


# ── ops stream ───────────────────────────────────────────────────────────────
def test_every_run_produces_a_tagged_record_readable_by_others(tmp_path, monkeypatch):
    """The stream is shared with the logger, so a reader must be able to tell an
    outcome from a log line without guessing at field names."""
    freeze(monkeypatch, MONDAY_0900)
    scheduler = build(tmp_path)
    scheduler.register(job("open_scan", at="0900"))
    scheduler.tick()
    assert scheduler.wait_for_idle()

    outcomes = read_outcomes(tmp_path / "ops.jsonl")
    assert len(outcomes) == 1
    assert outcomes[0].key == "open_scan"
    assert outcomes[0].started_at.tzinfo is not None
    assert outcomes[0].duration_seconds >= 0


def test_an_unparseable_outcome_record_raises_rather_than_shrinking_the_count(tmp_path):
    """Skipping it would understate the failure count, and the failure count is what
    the alerts are computed from."""
    (tmp_path / "ops.jsonl").write_text(
        '{"kind": "job_outcome", "key": "x"}\n{"kind": "job_outcome", "key": "y"}\n'
    )
    with pytest.raises(StateUnavailable):
        read_outcomes(tmp_path / "ops.jsonl")


# ── lifecycle ────────────────────────────────────────────────────────────────
def test_stop_ends_the_loop_and_in_flight_jobs_are_allowed_to_finish(
    tmp_path, monkeypatch
):
    """SIGTERM during a deploy must not sever a job halfway through submitting."""
    freeze(monkeypatch, MONDAY_0900)
    scheduler = build(tmp_path, poll_seconds=0.01, shutdown_timeout_seconds=5.0)
    started = threading.Event()
    release = threading.Event()

    def slow() -> JobResult:
        started.set()
        release.wait(5)
        return JobResult.OK

    scheduler.register(job("slow", at="0900", run=slow))
    loop = threading.Thread(target=scheduler.serve, daemon=True)
    loop.start()

    assert started.wait(5)
    scheduler.stop()
    release.set()
    loop.join(timeout=5)
    assert not loop.is_alive(), "stop() must break the poll sleep, not wait it out"
    assert [o.result for o in scheduler.outcomes()] == [JobResult.OK]


def test_serve_stops_after_max_ticks_without_a_signal(tmp_path, monkeypatch):
    freeze(monkeypatch, MONDAY_0900)
    scheduler = build(tmp_path)
    scheduler.register(job("open_scan", at="0900"))
    scheduler.serve(max_ticks=2)
    assert scheduler.ticks == 2
    assert len(scheduler.outcomes()) == 1


def test_signal_handler_installation_reports_what_it_managed(tmp_path):
    """Only the main thread may install handlers. Refusing to start under a test
    harness would be worse than running without them, so the failure is reported
    rather than raised."""
    scheduler = build(tmp_path)
    result: dict[str, tuple[int, ...]] = {}
    worker = threading.Thread(
        target=lambda: result.update(installed=scheduler.install_signal_handlers())
    )
    worker.start()
    worker.join()
    assert result["installed"] == ()
