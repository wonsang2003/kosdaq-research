"""Scheduling and operations types.

The scheduler being modelled is a twenty-second poll loop over a flat list of
`(HHMM, key, callable)` triples. That is not a placeholder for something better — it
is the design, and it earns its place:

  * No dependency, no daemon, no cron syntax to get wrong.
  * The registration table is readable top to bottom, so "what does this box do at
    15:30" is answered by looking rather than by querying.
  * A test can parse the table out of the source and assert ordering invariants —
    which is how a real key-collision bug was caught, where two jobs shared a
    deduplication key and the second only ran on days the process happened to restart.

What the original got wrong, and this fixes: its `last`-fired map lived only in
memory, so a restart re-armed every job whose minute had not yet passed. Here it is
persisted.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

_HHMM = re.compile(r"^([01]\d|2[0-3])[0-5]\d$")


class JobResult(str, Enum):
    """How a job run ended."""

    OK = "OK"
    SKIPPED = "SKIPPED"
    """Ran, decided there was nothing to do, and said so. Distinct from OK because a
    job that skips every day is broken in a way a success count cannot show."""

    FAILED = "FAILED"

    UNDECIDABLE = "UNDECIDABLE"
    """The job could not determine whether it should act — a calendar lookup that
    returned neither yes nor no, a feed that was down.

    Its own state because the rule is *not recording is safer than recording wrong*.
    Collapsing this into SKIPPED loses the distinction between "nothing to do" and
    "could not tell", and it was that exact collapse — treating `None` as `False` —
    that produced this project's worst class of silent failure.
    """


class ScheduledJob(BaseModel):
    """One entry in the registration table."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    at: str
    """Fire time as `HHMM`, exchange-local."""

    key: str = Field(min_length=2, max_length=24)
    """Deduplication key, unique across the whole table.

    Uniqueness is enforced by the registry rather than by convention: a duplicate key
    means the second job silently never fires, which is invisible in logs because
    nothing errors and nothing runs.
    """

    run: Callable[[], JobResult]

    description: str = ""

    weekdays_only: bool = True
    """Weekend gate. Holidays are *not* handled here — a job asks the calendar and
    returns UNDECIDABLE if the answer is unknown."""

    @field_validator("at")
    @classmethod
    def _valid_hhmm(cls, value: str) -> str:
        if not _HHMM.match(value):
            raise ValueError(f"fire time must be HHMM, got {value!r}")
        return value


class JobOutcome(BaseModel):
    """The record of one run, appended to the ops stream."""

    model_config = ConfigDict(frozen=True)

    key: str
    started_at: datetime
    finished_at: datetime
    result: JobResult
    detail: str = ""

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()


class AlertSeverity(str, Enum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"


class AlertStatus(str, Enum):
    """Triage state — the anti-alert-fatigue mechanism.

    Without this a dashboard accumulates known-and-accepted rows until nobody reads
    it, at which point the alerting system is worse than none because it creates the
    impression of monitoring.
    """

    OPEN = "open"
    """Needs action now."""

    FIXED = "fixed"
    """Root cause shipped; historical rows remain and will age out."""

    DESIGN = "design"
    """Intentional. A decision was made and this is its expected consequence."""


class OpsAlert(BaseModel):
    """One operational finding."""

    model_config = ConfigDict(frozen=True)

    severity: AlertSeverity
    status: AlertStatus
    category: str
    what: str
    detail: str = ""
    count: int = 0

    action: str = ""
    """Remediation, carried with the alert. An alert without an action is a fact, and
    facts do not wake anyone up usefully."""

    @property
    def sort_key(self) -> tuple[int, int]:
        """Open first, then by severity. Read by the dashboard so the top row is
        always the one that needs a human."""
        order = {AlertSeverity.CRITICAL: 0, AlertSeverity.MAJOR: 1, AlertSeverity.MINOR: 2}
        return (0 if self.status is AlertStatus.OPEN else 1, order[self.severity])


class HealthReport(BaseModel):
    """What `/health` and `/api/version` answer with."""

    model_config = ConfigDict(frozen=True)

    build_sha: str
    """Hash of the code **this process loaded**, computed once at import.

    Computing it per request would re-read the file on disk and therefore always
    match, which defeats the entire check. The incident behind it: a deploy died
    between upload and restart, leaving new code on disk and hours-old code in
    memory, and `systemctl is-active` reported healthy — because a stale process is
    still active.
    """

    disk_sha: str
    """Hash of the file on disk right now."""

    booted_at: datetime
    uptime_seconds: float
    mode: str
    kill_switch_engaged: bool

    @property
    def is_stale(self) -> bool:
        """True when the running code is not the code on disk."""
        return bool(self.build_sha and self.disk_sha and self.build_sha != self.disk_sha)

    @property
    def is_healthy(self) -> bool:
        return not self.is_stale
