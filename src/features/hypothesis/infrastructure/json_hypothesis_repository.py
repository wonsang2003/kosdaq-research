"""JSON-file implementation of the hypothesis ledger.

Reads and writes the append-only `ledger.json` produced by the falsification
harness. The path is injected rather than hard-coded — the original module carried
an absolute path on my own machine, which is what made the harness
unrunnable elsewhere and untestable without mutating the real ledger.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from src.features.falsification.domain.entities.verdict import Outcome, Verdict
from src.features.hypothesis.domain.entities.cause_of_death import CauseOfDeath
from src.features.hypothesis.domain.entities.hypothesis import EdgeMechanism, Hypothesis
from src.features.hypothesis.domain.repositories.hypothesis_repository import (
    HypothesisRepository,
)
from src.shared.domain.errors import DuplicateHypothesis, LedgerUnavailable

_GATE_TO_CAUSE: dict[str, CauseOfDeath] = {
    "look_ahead": CauseOfDeath.UNOBSERVABLE_AT_DECISION,
    "recompute": CauseOfDeath.NOTHING_THERE,
    "placebo": CauseOfDeath.NOTHING_THERE,
    "random": CauseOfDeath.NOTHING_THERE,
    "walk_forward": CauseOfDeath.REGIME_CONCENTRATION,
    "deflated_significance": CauseOfDeath.MULTIPLICITY,
    "insufficient_data": CauseOfDeath.NOTHING_THERE,
}
"""Which taxonomy class each battery gate implies when it is the one that fails.

Note what is absent: no gate maps to FILL_FANTASY, COST_FLOOR, or
INSTRUMENT_ABSENT. The automated battery cannot detect those — they were all found
by hand, by placing real orders and watching them not fill. That gap is the honest
limit of this harness and the reason `retail_tradable` is tracked as a separate
field rather than folded into `survives_alpha`.
"""


class JsonHypothesisRepository(HypothesisRepository):
    """Ledger backed by a single JSON array on disk."""

    def __init__(self, ledger_path: Path | str) -> None:
        self._path = Path(ledger_path)

    # ── reads ────────────────────────────────────────────────────────────────
    def _rows(self) -> list[dict]:
        if not self._path.exists():
            raise LedgerUnavailable(f"ledger not found: {self._path}")
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LedgerUnavailable(f"ledger unreadable: {self._path}") from exc
        if not isinstance(data, list):
            raise LedgerUnavailable(f"ledger must be a JSON array: {self._path}")
        return data

    def find_by_key(self, key: str) -> Hypothesis | None:
        for row in self._rows():
            if row.get("key") == key:
                return self._to_hypothesis(row)
        return None

    def seen_keys(self) -> set[str]:
        return {row["key"] for row in self._rows() if "key" in row}

    def count_tests(self) -> int:
        return len(self._rows())

    def graveyard(self) -> list[Verdict]:
        return [self._to_verdict(r) for r in self._rows() if not r.get("survives_alpha")]

    def survivors(self) -> list[Verdict]:
        """Verdicts that cleared every alpha gate.

        Not part of the interface because it is a reporting convenience, but worth
        having: in the shipped ledger this returns seven entries, all with
        `retail_tradable` false. The gap between those two counts is the finding.
        """
        return [self._to_verdict(r) for r in self._rows() if r.get("survives_alpha")]

    # ── writes ───────────────────────────────────────────────────────────────
    def record(self, hypothesis: Hypothesis, verdict: Verdict) -> None:
        try:
            rows = self._rows()
        except LedgerUnavailable:
            if self._path.exists():
                raise
            rows = []

        if any(r.get("key") == hypothesis.key for r in rows):
            raise DuplicateHypothesis(hypothesis.key)

        rows.append({
            "ts": (hypothesis.registered_at or datetime.now()).isoformat(timespec="seconds"),
            "key": hypothesis.key,
            "name": hypothesis.name,
            "signal": hypothesis.signal,
            "survives_alpha": verdict.survives_alpha,
            "retail_tradable": verdict.retail_tradable,
            "killed_by": list(verdict.killed_by),
            "deflated_t_required": verdict.deflated_t_required,
        })
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            raise LedgerUnavailable(f"ledger unwritable: {self._path}") from exc

    # ── mapping ──────────────────────────────────────────────────────────────
    @staticmethod
    def _to_hypothesis(row: dict) -> Hypothesis:
        return Hypothesis(
            key=row["key"],
            name=row.get("name", row["key"]),
            signal=row.get("signal"),
            mechanism=EdgeMechanism(
                statement=row.get("mechanism", "not recorded"),
            ),
        )

    @staticmethod
    def _to_verdict(row: dict) -> Verdict:
        killed = list(row.get("killed_by") or [])
        survived = bool(row.get("survives_alpha"))
        tradable = bool(row.get("retail_tradable"))

        if survived and tradable:
            outcome = Outcome.CONFIRMED
            cause = None
        elif survived and not tradable:
            # Real market-neutral edge that long-only net cannot reach after
            # friction. The battery has no gate for this, so it is assigned here.
            outcome = Outcome.REFUTED
            cause = CauseOfDeath.COST_FLOOR
        else:
            outcome = Outcome.REFUTED
            cause = next(
                (_GATE_TO_CAUSE[g] for g in killed if g in _GATE_TO_CAUSE),
                CauseOfDeath.NOTHING_THERE,
            )

        return Verdict(
            hypothesis_key=row["key"],
            outcome=outcome,
            survives_alpha=survived,
            retail_tradable=tradable,
            killed_by=killed,
            cause_of_death=cause,
            deflated_t_required=row.get("deflated_t_required"),
        )
