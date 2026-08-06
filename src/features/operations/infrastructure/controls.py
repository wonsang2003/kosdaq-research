"""File-backed operator controls: the kill switch and the mode selector.

See `domain/entities/trading_mode.py` for why these are files.

One asymmetry here is deliberate and is the most important line in the module:
**the kill switch stops order submission and does not stop liquidation.** A switch
that froze everything would strand open positions with no way to close them, which
converts a "stop trading" decision into an unhedged overnight hold. Stopping new
risk and leaving the exit path open is what an operator actually wants at the moment
they reach for this.
"""

from __future__ import annotations

from pathlib import Path

from src.features.operations.domain.entities.trading_mode import TradingMode


class FileKillSwitch:
    """Halt new risk. Tripped by the existence of a file.

    Existence rather than contents, so `touch` is enough and there is no parsing step
    that could fail open. Survives restarts by construction.
    """

    def __init__(self, flag_path: Path | str) -> None:
        self._path = Path(flag_path)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def engaged(self) -> bool:
        """True when new orders must be refused.

        Checked fresh on every call — caching this would mean an operator's `touch`
        does not take effect until the next restart, which defeats the mechanism.
        """
        return self._path.exists()

    def reason(self) -> str:
        """Operator note, if one was written into the flag file.

        :return: the file's contents, or a default. Contents are optional; the file
            existing is the signal.
        """
        if not self._path.exists():
            return ""
        try:
            note = self._path.read_text(encoding="utf-8").strip()
        except OSError:
            return "kill switch engaged (note unreadable)"
        return note or "kill switch engaged"

    def engage(self, note: str = "") -> None:
        """Trip the switch. Idempotent."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(note, encoding="utf-8")

    def release(self) -> None:
        """Clear the switch. Idempotent — releasing an unengaged switch is not an error."""
        self._path.unlink(missing_ok=True)

    def guard_submission(self) -> None:
        """Raise if new orders are not permitted.

        Called at the top of every order-*placing* path and deliberately **not** on
        liquidation paths.

        :raise KillSwitchEngaged: when the switch is tripped.
        """
        if self.engaged:
            raise KillSwitchEngaged(self.reason())


class KillSwitchEngaged(RuntimeError):
    """Order submission was refused because the kill switch is engaged."""


class FileTradingMode:
    """Mode selector backed by a one-line file.

    Read fresh per call for the same reason as the kill switch: an operator changing
    the file expects the next job to see it, not the next deploy.
    """

    def __init__(self, mode_path: Path | str) -> None:
        self._path = Path(mode_path)

    @property
    def path(self) -> Path:
        return self._path

    def current(self) -> TradingMode:
        """Resolve the active mode.

        :return: the parsed mode. A missing, empty, unreadable, or unrecognised file
            resolves to `DRY` — see the module docstring for why this is the one
            fallback in the codebase that is correct.
        """
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError:
            return TradingMode.DRY
        return TradingMode.parse(raw)

    def set(self, mode: TradingMode) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(mode.value + "\n", encoding="utf-8")
