"""Foundation primitives: clock, atomic state, operator controls.

Each test names the production incident it guards. These are the pieces every other
module sits on, so a defect here is a defect everywhere and is correspondingly hard
to see.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import pytest

from src.features.operations.domain.entities.trading_mode import TradingMode
from src.features.operations.infrastructure.atomic_store import (
    AtomicJsonStore,
    JsonlAppender,
    StateUnavailable,
)
from src.features.operations.infrastructure.controls import (
    FileKillSwitch,
    FileTradingMode,
    KillSwitchEngaged,
)
from src.shared.domain import clock


# ── clock ────────────────────────────────────────────────────────────────────
def test_now_is_always_timezone_aware():
    """A naive datetime escaping into a comparison is the bug this module prevents."""
    assert clock.now().tzinfo is not None


def test_the_zone_is_stated_not_inherited_from_the_host():
    """The imitated system used naive `datetime.now()` in thirteen places with
    09:00-15:30 as an implicit *local* window. In a UTC container every session gate
    would fire nine hours late — and a gate that never opens logs nothing.

    This test passes under `TZ=UTC` precisely because the zone is explicit.
    """
    assert str(clock.MARKET_TZ) == "Asia/Seoul"
    utc_noon = datetime(2026, 6, 15, 3, 0, tzinfo=timezone.utc)  # 12:00 KST
    assert clock.hhmm(utc_noon) == "1200"
    assert clock.is_within_session(utc_noon)


def test_naive_input_raises_rather_than_being_assumed_local():
    naive = datetime(2026, 6, 15, 12, 0)
    with pytest.raises(ValueError, match="naive datetime"):
        clock.hhmm(naive)
    with pytest.raises(ValueError, match="naive datetime"):
        clock.day_key(naive)


@pytest.mark.parametrize(
    ("utc_hour", "utc_minute", "open_"),
    [
        (23, 0, False),   # 08:00 KST next day — before the open
        (0, 0, True),     # 09:00 KST — the open itself, inclusive
        (1, 0, True),     # 10:00 KST
        (6, 0, True),     # 15:00 KST
        (6, 30, True),    # 15:30 KST — the close itself, inclusive
        (6, 31, False),   # 15:31 KST
        (7, 0, False),    # 16:00 KST
    ],
)
def test_session_window_in_utc_terms(utc_hour, utc_minute, open_):
    """09:00-15:30 KST is 00:00-06:30 UTC. Stated in UTC deliberately: if the zone
    conversion were ever dropped, this test would fail rather than quietly pass on a
    machine that happens to be set to Seoul time."""
    moment = datetime(2026, 6, 15, utc_hour, utc_minute, tzinfo=timezone.utc)
    assert clock.is_within_session(moment) is open_


def test_weekends_are_not_sessions():
    saturday = datetime(2026, 6, 20, 3, 0, tzinfo=timezone.utc)
    assert not clock.is_within_session(saturday)


def test_minutes_since_open_is_none_outside_the_session():
    """None, not 0 and not negative. 'Before the open' and 'at the open' are
    different states, and collapsing them hides scheduling bugs."""
    before = datetime(2026, 6, 15, 0, 0, tzinfo=timezone.utc)  # 09:00 KST exactly
    assert clock.minutes_since_open(before) == 0
    way_before = datetime(2026, 6, 14, 20, 0, tzinfo=timezone.utc)
    assert clock.minutes_since_open(way_before) is None


def test_the_clock_can_be_frozen_with_one_line(monkeypatch):
    """The whole reason there is exactly one time accessor."""
    frozen = datetime(2026, 3, 2, 10, 30, tzinfo=clock.MARKET_TZ)
    monkeypatch.setattr(clock, "now", lambda: frozen)
    assert clock.hhmm() == "1030"
    assert clock.day_key() == "2026-03-02"


# ── atomic state ─────────────────────────────────────────────────────────────
def test_save_is_atomic_and_leaves_no_temp_file(tmp_path):
    store = AtomicJsonStore(tmp_path / "state.json")
    store.save({"a": 1})
    assert store.load() == {"a": 1}
    assert list(tmp_path.glob("*.tmp")) == []


def test_a_corrupt_existing_file_raises_instead_of_returning_a_default(tmp_path):
    """Silently substituting `{}` for corrupt state destroys the evidence and, worse,
    reads as 'no open positions' — which is how a system re-enters a position it is
    already holding."""
    path = tmp_path / "state.json"
    path.write_text("{not json")
    with pytest.raises(StateUnavailable):
        AtomicJsonStore(path).load()


def test_a_missing_file_returns_the_default(tmp_path):
    """Missing and corrupt are different. Missing is an ordinary first run."""
    assert AtomicJsonStore(tmp_path / "absent.json").load() == {}
    assert AtomicJsonStore(tmp_path / "absent.json").load(default=[]) == []


def test_mutate_is_read_modify_write_under_a_lock(tmp_path):
    store = AtomicJsonStore(tmp_path / "s.json")
    store.save({"n": 1})
    store.mutate(lambda d: {**d, "n": d["n"] + 1})
    assert store.load() == {"n": 2}


def test_overwriting_never_leaves_a_truncated_file(tmp_path):
    """The failure mode being prevented: the imitated system opened the target with
    mode 'w', so a crash between truncate and write left a zero-length file where a
    position used to be."""
    store = AtomicJsonStore(tmp_path / "s.json")
    store.save({"big": "x" * 10_000})
    store.save({"small": 1})
    assert store.load() == {"small": 1}
    assert store.path.stat().st_size > 0


# ── journal ──────────────────────────────────────────────────────────────────
def test_journal_appends_and_reads_back_in_order(tmp_path):
    journal = JsonlAppender(tmp_path / "j.jsonl")
    for i in range(3):
        journal.append({"i": i})
    assert [r["i"] for r in journal.read_all()] == [0, 1, 2]


def test_a_torn_final_line_loses_one_record_not_the_file(tmp_path):
    """A process killed mid-append leaves a partial last line. That is expected, not
    corruption — the preceding records must still be recoverable."""
    path = tmp_path / "j.jsonl"
    journal = JsonlAppender(path)
    journal.append({"i": 0})
    journal.append({"i": 1})
    with open(path, "a") as handle:
        handle.write('{"i": 2, "part')
    assert [r["i"] for r in journal.read_all()] == [0, 1]


def test_corruption_in_the_middle_raises(tmp_path):
    """Unlike a torn tail, a bad line in the middle means something else went wrong."""
    path = tmp_path / "j.jsonl"
    path.write_text('{"i":0}\nGARBAGE\n{"i":2}\n')
    with pytest.raises(StateUnavailable, match="corrupt journal line 2"):
        JsonlAppender(path).read_all()


def test_an_unwritable_journal_raises_rather_than_dropping_the_record(tmp_path):
    """Downstream code recovers state from this file. A journal that silently drops
    writes is worse than no journal, because it is trusted."""
    blocked = tmp_path / "ro"
    blocked.mkdir()
    os.chmod(blocked, 0o500)
    try:
        with pytest.raises(StateUnavailable):
            JsonlAppender(blocked / "j.jsonl").append({"a": 1})
    finally:
        os.chmod(blocked, 0o700)


def test_journal_records_are_fsynced(tmp_path):
    """Buffered writes are lost on a hard kill, and these records are the only
    evidence that an order was placed."""
    import inspect

    source = inspect.getsource(JsonlAppender.append)
    assert "fsync" in source


# ── operator controls ────────────────────────────────────────────────────────
def test_kill_switch_is_tripped_by_a_file_existing(tmp_path):
    switch = FileKillSwitch(tmp_path / "kill.flag")
    assert not switch.engaged
    switch.engage("manual halt")
    assert switch.engaged
    assert switch.reason() == "manual halt"


def test_kill_switch_survives_a_restart(tmp_path):
    """The point of a file. An in-memory flag is cleared by the restart that an
    incident usually involves."""
    path = tmp_path / "kill.flag"
    FileKillSwitch(path).engage()
    assert FileKillSwitch(path).engaged


def test_an_empty_flag_file_still_counts_as_engaged(tmp_path):
    """`touch` must be enough. Requiring contents adds a parsing step that could
    fail open, which is the one direction this must never fail."""
    path = tmp_path / "kill.flag"
    path.write_text("")
    assert FileKillSwitch(path).engaged


def test_guard_submission_raises_when_engaged(tmp_path):
    switch = FileKillSwitch(tmp_path / "kill.flag")
    switch.guard_submission()  # no-op when clear
    switch.engage("halt")
    with pytest.raises(KillSwitchEngaged, match="halt"):
        switch.guard_submission()


def test_release_is_idempotent(tmp_path):
    switch = FileKillSwitch(tmp_path / "kill.flag")
    switch.release()
    switch.release()
    assert not switch.engaged


@pytest.mark.parametrize(
    ("contents", "expected"),
    [
        ("LIVE", TradingMode.LIVE), ("live\n", TradingMode.LIVE),
        (" Paper ", TradingMode.PAPER), ("DRY", TradingMode.DRY),
        ("", TradingMode.DRY), ("LIV", TradingMode.DRY), ("nonsense", TradingMode.DRY),
    ],
)
def test_mode_parsing_defaults_to_the_safe_value(tmp_path, contents, expected):
    """A typo in the mode file must become a no-op, not an outage — and never an
    accidental promotion to LIVE."""
    path = tmp_path / "mode.txt"
    path.write_text(contents)
    assert FileTradingMode(path).current() is expected


def test_a_missing_mode_file_is_dry(tmp_path):
    assert FileTradingMode(tmp_path / "absent.txt").current() is TradingMode.DRY


def test_mode_is_read_fresh_every_time(tmp_path):
    """An operator changing the file expects the next job to see it, not the next
    deploy. Caching would defeat the whole mechanism."""
    path = tmp_path / "mode.txt"
    selector = FileTradingMode(path)
    path.write_text("DRY")
    assert selector.current() is TradingMode.DRY
    path.write_text("PAPER")
    assert selector.current() is TradingMode.PAPER


def test_only_live_places_real_orders():
    assert TradingMode.LIVE.places_real_orders
    assert not TradingMode.PAPER.places_real_orders
    assert not TradingMode.DRY.places_real_orders
    assert not TradingMode.DRY.touches_network
