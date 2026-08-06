"""Runtime configuration, resolved once and validated at startup.

Fail-fast rather than fail-late. The system this imitates read configuration lazily
with `float(os.getenv(...))` scattered through constructors, so a typo in a numeric
setting surfaced as a `ValueError` from deep inside a request path — hours after the
process started, and only on the code path that happened to need it.

Everything here resolves at import of the composition root, so a misconfiguration is
a startup failure with a message, not a 3am traceback.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

REPO_ROOT = Path(__file__).resolve().parents[2]


class AppConfig(BaseModel):
    """Everything the composition root needs to build the system."""

    model_config = ConfigDict(frozen=True)

    data_dir: Path = REPO_ROOT / "data" / "runtime"
    """Everything the running system writes. Separate from `data/samples` (shipped
    inputs) and `data/audit` (shipped evidence) so that running the service can never
    modify anything the repository asserts about itself."""

    sample_panel: Path = REPO_ROOT / "data" / "samples" / "SYNTHETIC_minute_bars.parquet"

    strategy_key: str = "refuted_hour"
    """Defaults to the refuted rule. There is no configuration value that selects a
    working strategy, because no working strategy is in this repository."""

    initial_cash: float = Field(default=10_000_000.0, gt=0)
    """₩10M — the account size the research was actually sized against, which is why
    capacity limits show up in the results rather than being abstracted away."""

    max_signals_per_evaluation: int = Field(default=5, ge=1)
    order_notional: float = Field(default=1_000_000.0, gt=0)

    interval_minutes: int = Field(default=1, ge=1)
    """Bar interval to load. Must match what the panel actually holds — the data
    source raises rather than returning an empty list when asked for an interval it
    never collected, which is why this is stated here instead of being guessed."""

    evaluation_step_minutes: int = Field(default=5, ge=1)
    """How often the strategy is asked. Coarser than the bar interval on purpose:
    evaluating every bar is neither realistic for a session runner nor informative,
    and it inflates the evaluation count in a way that flatters nothing."""
    http_port: int = Field(default=8790, ge=1024, le=65535)
    """Deliberately not 8789 — that port belongs to a different, live service on the
    author's machine, and a portfolio repository that fights a running system for a
    port is a portfolio repository that gets run once."""

    poll_seconds: float = Field(default=20.0, gt=0)

    @property
    def journal_path(self) -> Path:
        return self.data_dir / "orders.jsonl"

    @property
    def scheduler_state_path(self) -> Path:
        return self.data_dir / "scheduler_state.json"

    @property
    def ops_events_path(self) -> Path:
        return self.data_dir / "ops_events.jsonl"

    @property
    def job_outcomes_path(self) -> Path:
        return self.data_dir / "job_outcomes.jsonl"

    @property
    def bar_store_dir(self) -> Path:
        return self.data_dir / "bars"

    @property
    def kill_switch_path(self) -> Path:
        return self.data_dir / "kill.flag"

    @property
    def mode_path(self) -> Path:
        return self.data_dir / "mode.txt"

    @classmethod
    def from_env(cls) -> AppConfig:
        """Build from environment, falling back to defaults.

        Only a handful of settings are overridable, on purpose. A configuration
        surface wide enough to change behaviour meaningfully is a second, undocumented
        API — and the values that matter here are the ones a reviewer should see in
        the source rather than have to discover.
        """
        overrides: dict = {}
        if raw := os.environ.get("KQR_DATA_DIR"):
            overrides["data_dir"] = Path(raw)
        if raw := os.environ.get("KQR_STRATEGY"):
            overrides["strategy_key"] = raw
        if raw := os.environ.get("KQR_PORT"):
            try:
                overrides["http_port"] = int(raw)
            except ValueError as exc:
                raise ValueError(f"KQR_PORT must be an integer, got {raw!r}") from exc
        return cls(**overrides)
