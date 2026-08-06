"""Command-line entry point.

Every operation the scheduler can fire is also reachable here as a single command.
That is not convenience — it is what makes an incident survivable. Replay, backfill,
and "run the settle job again because it silently did nothing" all need to happen
without waiting for a clock, and a system whose jobs only exist inside a scheduler
loop cannot do any of them.

Stdlib argparse rather than a framework, because a CLI that adds a dependency to the
install path is a CLI that a reviewer does not get to run.
"""

from __future__ import annotations

import argparse
import sys

from src.app.config import AppConfig
from src.app.use_cases.run_backtest import run_backtest
from src.app.use_cases.run_session import run_session
from src.app.wiring import build
from src.features.strategy.infrastructure.registry import available_keys


def _build(args: argparse.Namespace):
    overrides = {}
    if getattr(args, "strategy", None):
        overrides["strategy_key"] = args.strategy
    return build(AppConfig(**overrides) if overrides else None)


def cmd_run(args) -> int:
    """Replay one session end to end."""
    system = _build(args)
    print(system.banner(), end="\n\n")
    print(run_session(system).summary())
    return 0


def cmd_backtest(args) -> int:
    """Replay every session and judge on the session count."""
    system = _build(args)
    print(system.banner(), end="\n\n")
    print(run_backtest(system, max_sessions=args.sessions).summary())
    return 0


def cmd_status(args) -> int:
    """What the system currently believes about itself."""
    system = _build(args)
    print(system.banner())
    try:
        positions = {s: p.quantity for s, p in system.broker.positions().items() if p.quantity}
    except Exception as exc:  # BrokerUnavailable and anything else
        print(f"positions      UNKNOWN — {exc}")
        print("               (refusing to report flat when state is unknown; a false")
        print("                flat is how a system re-enters a position it already holds)")
        return 1
    print(f"positions      {positions or 'flat'}")
    print(f"cash           {system.broker.cash:,.0f}")
    return 0


def cmd_strategies(_args) -> int:
    """List what can be plugged in."""
    from src.features.strategy.infrastructure.registry import get_strategy

    print("Registered strategies:\n")
    for key in available_keys():
        spec = get_strategy(key).spec
        print(f"  {key:<18} [{spec.verdict}] {spec.name}")
        print(f"  {'':<18} {spec.summary[:96]}")
        if spec.evidence:
            print(f"  {'':<18} evidence: {spec.evidence}")
        print()
    print(
        "No CONFIRMED strategy is registered, and none will be. The strategies this\n"
        "project confirmed are live on the author's own capital and are not in this\n"
        "repository — see docs/DISCLOSURE.md. What ships is a refuted rule and a\n"
        "random control, which is enough to exercise every interface end to end."
    )
    return 0


def cmd_kill(args) -> int:
    """Engage or release the kill switch."""
    system = _build(args)
    if args.release:
        system.kill_switch.release()
        print("kill switch released")
    else:
        system.kill_switch.engage(args.note or "engaged from the CLI")
        print(f"kill switch ENGAGED — {system.kill_switch.reason()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kqr",
        description="KOSDAQ quantitative research runtime — offline, no credentials required.",
    )
    parser.add_argument("--strategy", help=f"one of: {', '.join(available_keys())}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("run", help="replay one session").set_defaults(func=cmd_run)

    backtest = subparsers.add_parser("backtest", help="replay every session, day-clustered")
    backtest.add_argument("--sessions", type=int, default=None, help="cap the number replayed")
    backtest.set_defaults(func=cmd_backtest)

    subparsers.add_parser("status", help="current mode, positions, cash").set_defaults(
        func=cmd_status
    )
    subparsers.add_parser("strategies", help="what can be plugged in").set_defaults(
        func=cmd_strategies
    )

    kill = subparsers.add_parser("kill", help="engage or release the kill switch")
    kill.add_argument("--release", action="store_true")
    kill.add_argument("--note", default="")
    kill.set_defaults(func=cmd_kill)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
