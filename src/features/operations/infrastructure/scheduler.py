"""The twenty-second poll loop, with the two defects of the original removed.

The shape is deliberately unchanged from the system this imitates: a flat registry of
`(HHMM, key, run)` triples, polled every twenty seconds, matched on the minute. No
cron expression, no daemon, no dependency. `docs`-free operators answer "what does
this box do at 15:30" by reading the table.

Two things about the original are not kept.

**The last-fired map is persisted.** It lived in a process-local dict, so a restart
re-armed every job whose minute had not yet elapsed. Restarting at 09:00:10 — which
is exactly when a restart happens, because 09:00 is when things break — re-fired the
09:00 job ten seconds after it had already run. Here the map goes through
`AtomicJsonStore`, so the dedup decision outlives the process that made it.

**A duplicate key is refused at registration.** Two jobs once shared a key; the second
never fired, and nothing anywhere said so. No exception, no log line, no missing-run
alert — the only symptom was an absence, and absences are the failure class this
repository exists to make visible. `register` now raises.

Two further choices worth stating, because both look like bugs until you know why:

*At-most-once, not at-least-once.* The fired mark is persisted **before** the job
runs. A process killed mid-run therefore does not re-run the job on restart. That is
the safe direction: a re-fired entry job places a second order, while a job that
failed to complete shows up as silence, and `ops_alerts.build_alerts` raises silence
as an alert. Losing a run loudly beats duplicating one quietly.

*No catch-up.* A job whose minute passed while the process was down does not fire
late. Firing the 09:00 open job at 09:47 would submit an order priced for a moment
that has gone; the missed run is surfaced by the silence alert instead of being
guessed at.
"""

from __future__ import annotations

import signal as signal_module
import sys
import threading
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from src.features.operations.domain.entities.scheduled_job import (
    JobOutcome,
    JobResult,
    ScheduledJob,
)
from src.features.operations.infrastructure.atomic_store import (
    AtomicJsonStore,
    JsonlAppender,
    StateUnavailable,
)
from src.shared.domain import clock
from src.shared.domain.errors import DomainError

JOB_OUTCOME_KIND = "job_outcome"
"""Discriminator on every record this module writes.

The ops stream is shared with `logging.StructuredLogger`, so a reader must be able to
tell a job outcome from a captured log line without guessing at field names.
"""


class DuplicateJobKey(DomainError):
    """A key already in the registry was registered again.

    Raised rather than overwriting or ignoring, because both of those reproduce the
    original bug: one job silently stops existing and the deduplication key it shares
    makes the survivor's run look like the loser's.
    """


class UnknownJob(DomainError):
    """`run_once` was asked for a key that is not registered.

    Not a no-op: an operator typing the wrong key at 3am must be told, not left
    believing a job ran.
    """


class Scheduler:
    """Registry plus poll loop.

    Not thread-safe for registration — build the table at startup, then serve. Firing
    is thread-safe and cross-process safe, because the fired map is mutated under the
    store's advisory lock.
    """

    def __init__(
        self,
        state_path: Path | str,
        events_path: Path | str,
        poll_seconds: float = 20.0,
        shutdown_timeout_seconds: float = 30.0,
        stderr: TextIO | None = None,
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        if shutdown_timeout_seconds < 0:
            raise ValueError("shutdown_timeout_seconds must not be negative")
        self._store = AtomicJsonStore(state_path)
        self._events = JsonlAppender(events_path)
        self.poll_seconds = float(poll_seconds)
        self.shutdown_timeout_seconds = float(shutdown_timeout_seconds)
        self._jobs: dict[str, ScheduledJob] = {}
        self._threads: list[threading.Thread] = []
        self._threads_lock = threading.Lock()
        self._stopped = threading.Event()
        self._stderr = stderr if stderr is not None else sys.stderr
        self.ticks = 0
        self.internal_failures: list[str] = []
        """Failures of the scheduler's own machinery — an unwritable ops stream, a
        thread that would not join. Kept in memory *and* written to stderr because the
        one place a failure cannot be recorded is the recorder."""

    # ── registry ─────────────────────────────────────────────────────────────
    def register(self, job: ScheduledJob) -> ScheduledJob:
        """Add one entry to the table.

        :raise DuplicateJobKey: if the key is already present. See the class docstring
            for the incident.
        """
        if job.key in self._jobs:
            existing = self._jobs[job.key]
            raise DuplicateJobKey(
                f"job key {job.key!r} already registered at {existing.at} "
                f"({existing.description or 'no description'}); "
                "a shared key means one of the two jobs never fires"
            )
        self._jobs[job.key] = job
        return job

    def register_all(self, jobs: Iterable[ScheduledJob]) -> None:
        for job in jobs:
            self.register(job)

    @property
    def job_keys(self) -> tuple[str, ...]:
        """Registered keys in registration order — the input `ops_alerts` needs to
        notice a job that has gone silent."""
        return tuple(self._jobs)

    def jobs(self) -> tuple[ScheduledJob, ...]:
        return tuple(self._jobs.values())

    def table(self) -> tuple[tuple[str, str, str], ...]:
        """`(at, key, description)` sorted by fire time — the table an operator reads."""
        return tuple(
            (job.at, job.key, job.description)
            for job in sorted(self._jobs.values(), key=lambda j: (j.at, j.key))
        )

    # ── persisted deduplication ──────────────────────────────────────────────
    def fired_days(self) -> dict[str, str]:
        """Key → last fired `YYYY-MM-DD`, read from disk.

        Read fresh rather than cached: the CLI (`run_once`) and the loop are different
        processes, and a cached map would let a manual run be repeated automatically
        minutes later.

        :raise StateUnavailable: if the state file exists but is not the shape this
            writes. Deliberately not degraded to an empty map — an empty map re-arms
            every job, which is the exact failure this persistence exists to prevent.
        """
        document = self._store.load({})
        if not isinstance(document, dict):
            raise StateUnavailable(
                f"scheduler state is not an object: {self._store.path}"
            )
        fired = document.get("last_fired", {})
        if not isinstance(fired, dict):
            raise StateUnavailable(
                f"scheduler state 'last_fired' is not an object: {self._store.path}"
            )
        return {str(key): str(day) for key, day in fired.items()}

    def _mark_fired(self, key: str, day: str) -> None:
        """Record the fire under the store's lock.

        Read-modify-write without the lock loses one of two concurrent marks, and the
        loss is invisible because both writes succeed.
        """

        def change(document: Any) -> dict:
            if not isinstance(document, dict):
                raise StateUnavailable(
                    f"scheduler state is not an object: {self._store.path}"
                )
            fired = dict(document.get("last_fired") or {})
            fired[key] = day
            return {**document, "version": 1, "last_fired": fired}

        self._store.mutate(change, default={})

    # ── firing decision ──────────────────────────────────────────────────────
    def is_due(self, job: ScheduledJob, moment: datetime | None = None) -> bool:
        """Whether this job should fire at `moment`.

        Pure: reads the clock and the persisted map, changes nothing. Exposed so a
        test — and an operator's dry-run — can ask the question without side effects.
        """
        moment = moment or clock.now()
        local = moment.astimezone(clock.MARKET_TZ)
        if job.weekdays_only and local.weekday() >= 5:
            return False
        if clock.hhmm(local) != job.at:
            return False
        return self.fired_days().get(job.key) != clock.day_key(local)

    def tick(self, moment: datetime | None = None) -> list[str]:
        """One poll: dispatch everything due.

        :return: keys dispatched, in registration order. Dispatch is not completion —
            each job runs in its own daemon thread so that a job which blocks on a
            hung socket cannot stall the loop or its siblings.
        """
        moment = moment or clock.now()
        day = clock.day_key(moment)
        dispatched: list[str] = []
        for job in self._jobs.values():
            if not self.is_due(job, moment):
                continue
            self._mark_fired(job.key, day)  # before running: at-most-once
            self._spawn(job)
            dispatched.append(job.key)
        self.ticks += 1
        return dispatched

    def run_once(
        self, key: str, moment: datetime | None = None, mark_fired: bool = True
    ) -> JobOutcome:
        """Run one job now, ignoring its fire time, the weekday gate and the dedup map.

        Every job is independently runnable from a shell for the same reason every
        strategy is independently backtestable: a job you cannot invoke by hand is a
        job you cannot debug during the incident it caused.

        Runs synchronously and returns the outcome, because a CLI caller's exit code
        depends on it.

        :param mark_fired: default True, so a manual run suppresses the automatic one
            later the same day. An operator who ran the order job by hand at 09:05
            does not want it running again at 09:10.
        :raise UnknownJob: if the key is not registered.
        :raise StateUnavailable: if the outcome cannot be written to the ops stream —
            surfaced here rather than swallowed, since an unrecorded manual run is
            precisely the gap that makes an incident timeline unreconstructable.
        """
        job = self._jobs.get(key)
        if job is None:
            raise UnknownJob(
                f"no job registered with key {key!r}; known keys: {list(self._jobs)}"
            )
        if mark_fired:
            self._mark_fired(job.key, clock.day_key(moment or clock.now()))
        outcome = self._execute(job)
        self._record(outcome)
        return outcome

    # ── execution ────────────────────────────────────────────────────────────
    def _execute(self, job: ScheduledJob) -> JobOutcome:
        """Run the callable and classify the result. Never raises."""
        started = clock.now()
        detail = ""
        try:
            returned = job.run()
        except BaseException as exc:  # noqa: BLE001 - a job must not kill the loop
            result = JobResult.FAILED
            detail = f"{type(exc).__name__}: {exc}"
        else:
            if isinstance(returned, JobResult):
                result = returned
            else:
                # A job whose contract slipped returns None, and None is falsy, and a
                # falsy return recorded as OK is a success count that means nothing.
                result = JobResult.FAILED
                detail = (
                    f"job returned {returned!r} ({type(returned).__name__}), "
                    "expected a JobResult"
                )
        return JobOutcome(
            key=job.key,
            started_at=started,
            finished_at=clock.now(),
            result=result,
            detail=detail,
        )

    def _record(self, outcome: JobOutcome) -> None:
        self._events.append(
            {"kind": JOB_OUTCOME_KIND, **outcome.model_dump(mode="json")}
        )

    def _spawn(self, job: ScheduledJob) -> threading.Thread:
        thread = threading.Thread(
            target=self._run_and_record, args=(job,), name=f"job-{job.key}", daemon=True
        )
        with self._threads_lock:
            self._threads = [t for t in self._threads if t.is_alive()]
            self._threads.append(thread)
        thread.start()
        return thread

    def _run_and_record(self, job: ScheduledJob) -> None:
        try:
            self._record(self._execute(job))
        except BaseException as exc:  # noqa: BLE001 - recording must not kill the loop
            self._note_internal_failure(
                f"could not record outcome for {job.key!r}: {type(exc).__name__}: {exc}"
            )

    def _note_internal_failure(self, message: str) -> None:
        self.internal_failures.append(message)
        try:
            print(f"scheduler: {message}", file=self._stderr, flush=True)
        except Exception:  # noqa: BLE001 - stderr is the last resort; do not recurse
            pass

    # ── loop lifecycle ───────────────────────────────────────────────────────
    def serve(self, max_ticks: int | None = None) -> None:
        """Poll until `stop()`.

        Sleeps on an `Event` rather than `time.sleep` so a SIGTERM arriving one second
        into a twenty-second sleep is acted on immediately instead of at the end of
        the interval — a deploy that waits twenty seconds per restart gets skipped by
        the person deploying, and a skipped restart is a stale process.

        :param max_ticks: stop after this many polls. For tests and for a `--once`
            style CLI; None means until stopped.
        """
        self._stopped.clear()
        polls = 0
        while not self._stopped.is_set():
            try:
                self.tick()
            except BaseException as exc:  # noqa: BLE001 - the loop outlives its jobs
                self._note_internal_failure(f"tick failed: {type(exc).__name__}: {exc}")
            polls += 1
            if max_ticks is not None and polls >= max_ticks:
                break
            self._stopped.wait(self.poll_seconds)
        self.drain(self.shutdown_timeout_seconds)

    def stop(self) -> None:
        """Ask the loop to finish. Safe to call from a signal handler.

        Sets a flag and returns — no I/O, no locks. A handler that wrote a file could
        deadlock against the mutation it interrupted.
        """
        self._stopped.set()

    @property
    def stopping(self) -> bool:
        return self._stopped.is_set()

    def install_signal_handlers(
        self, signums: Sequence[int] = (signal_module.SIGTERM, signal_module.SIGINT)
    ) -> tuple[int, ...]:
        """Wire SIGTERM/SIGINT to `stop()`.

        :return: the signals actually installed. Python only permits this on the main
            thread; when called elsewhere (a test, an embedded runner) the failure is
            reported as an empty tuple rather than an exception, because a scheduler
            that refuses to start under a test harness is worse than one without
            handlers.
        """
        installed: list[int] = []
        for signum in signums:
            try:
                signal_module.signal(signum, lambda *_: self.stop())
            except (ValueError, OSError, RuntimeError):
                continue
            installed.append(signum)
        return tuple(installed)

    def drain(self, timeout_seconds: float | None = None) -> bool:
        """Let in-flight jobs finish.

        :return: True if every thread finished inside the budget. False is recorded as
            an internal failure and returned, not raised: at shutdown the process is
            leaving anyway, and the useful output is the *name* of the job that would
            not stop.
        """
        budget = (
            self.shutdown_timeout_seconds if timeout_seconds is None else timeout_seconds
        )
        deadline = clock.now().timestamp() + budget
        with self._threads_lock:
            pending = list(self._threads)
        for thread in pending:
            remaining = deadline - clock.now().timestamp()
            thread.join(timeout=max(0.0, remaining))
        stragglers = [t.name for t in pending if t.is_alive()]
        if stragglers:
            self._note_internal_failure(
                f"jobs still running after {budget}s shutdown budget: {stragglers}"
            )
            return False
        return True

    def wait_for_idle(self, timeout_seconds: float = 5.0) -> bool:
        """Block until dispatched jobs have finished. Same mechanics as `drain`,
        named for the thing tests actually want to say."""
        return self.drain(timeout_seconds)

    # ── reading back ─────────────────────────────────────────────────────────
    def outcomes(self) -> list[JobOutcome]:
        """Every recorded run, in write order.

        :raise StateUnavailable: if a record claims to be a job outcome but does not
            parse. Skipping it would understate the failure count, and the failure
            count is what the alerts are computed from.
        """
        return read_outcomes(self._events.path)


def read_outcomes(events_path: Path | str) -> list[JobOutcome]:
    """Parse job outcomes out of an ops stream that may also hold log events.

    :raise StateUnavailable: on a record that is tagged as an outcome and does not
        parse as one.
    """
    records = JsonlAppender(events_path).read_all()
    outcomes: list[JobOutcome] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict) or record.get("kind") != JOB_OUTCOME_KIND:
            continue
        payload = {k: v for k, v in record.items() if k != "kind"}
        try:
            outcomes.append(JobOutcome.model_validate(payload))
        except Exception as exc:  # noqa: BLE001 - re-raised as a state failure
            raise StateUnavailable(
                f"unparseable job outcome at record {index + 1} in {events_path}"
            ) from exc
    return outcomes
