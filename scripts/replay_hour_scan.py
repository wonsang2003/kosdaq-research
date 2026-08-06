#!/usr/bin/env python3
"""Replay the train-selection failure in my own intraday scanner.

This is the repository's central methodological claim, executable. It reads a result
file that my scanner produced in June, ranks twelve candidate configurations on the
training fold and again on the test fold, and shows what happened to the winner.

Standard library only. No network, no credentials, no third-party packages. Runs in
under a second.

    python3 scripts/replay_hour_scan.py

Background: the scanner scored KOSDAQ names intraday and the open question was which
hour of the session to trade. Twelve configurations were evaluated. I picked the one
that did best in training and wrote it into the strategy as `hour == 13`.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

ARTIFACT = Path(__file__).resolve().parents[1] / "data" / "audit" / "hour_scan_12_configs.json"
METRIC = "avg_exit_return_pct"


def load_configs(path: Path = ARTIFACT) -> list[dict]:
    """Read the scanner's own output and flatten it to one row per configuration.

    :return: rows of {label, train, test}, in file order.
    :raise FileNotFoundError: if the artifact is missing. Deliberately not caught —
        a replay that silently reports nothing is worse than one that fails.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for entry in payload["results"]:
        rows.append({
            "label": ",".join(entry["hours"]),
            "train": entry["train"][METRIC],
            "test": entry["test"][METRIC],
        })
    return rows


def pearson(xs: list[float], ys: list[float]) -> float:
    """Correlation between train and test performance across configurations.

    This is the number that decides whether selecting on train is a defensible
    procedure at all. If it is near zero, the training fold carries no information
    about the test fold and the selection is a coin flip dressed as a method.
    """
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    vx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    vy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return cov / (vx * vy) if vx and vy else float("nan")


def main() -> None:
    rows = load_configs()
    by_train = sorted(rows, key=lambda r: -r["train"])
    by_test = sorted(rows, key=lambda r: -r["test"])
    train_rank = {r["label"]: i + 1 for i, r in enumerate(by_train)}
    test_rank = {r["label"]: i + 1 for i, r in enumerate(by_test)}

    picked = by_train[0]
    losers = [r for r in rows if r["test"] < 0]
    corr = pearson([r["train"] for r in rows], [r["test"] for r in rows])

    print(f"\n  Configurations evaluated: {len(rows)}")
    print(f"  Metric: {METRIC} (percent per trade, after cost)\n")
    print(f"  {'hours':<10}{'train':>10}{'test':>10}{'rank tr':>9}{'rank te':>9}")
    print(f"  {'-' * 48}")
    for r in by_train:
        mark = "   <-- selected on train" if r["label"] == picked["label"] else ""
        print(f"  {r['label']:<10}{r['train']:>+10.3f}{r['test']:>+10.3f}"
              f"{train_rank[r['label']]:>9}{test_rank[r['label']]:>9}{mark}")

    print(f"\n  Configurations losing money out of sample: "
          f"{len(losers)} of {len(rows)}")
    print(f"  Train-selected config '{picked['label']}': "
          f"rank {train_rank[picked['label']]} on train, "
          f"rank {test_rank[picked['label']]} of {len(rows)} on test")
    print(f"  Pearson(train, test) across configurations: {corr:+.3f}")

    print("\n  Reading:")
    if len(losers) == len(rows):
        print("    Every configuration loses money out of sample. There was no hour to")
        print("    pick. The positive training numbers were the spread of noise across")
        print("    twelve draws, and selecting the maximum of that spread is not a")
        print("    method — it is the definition of the problem.")
    print(f"    A train/test correlation of {corr:+.3f} means the training fold carried")
    print("    almost no information about the test fold, so ranking on train was")
    print("    close to ranking at random.\n")


if __name__ == "__main__":
    main()
