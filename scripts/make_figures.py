#!/usr/bin/env python3
"""Generate the figures in docs/figures/ — pure standard library.

No matplotlib. The pinned dependency list is short on purpose: a reviewer who has to
install a plotting stack before the repository will build is a reviewer who does not
build it. A few hundred lines of SVG emitter costs less than that dependency.

The figures import the repository's own functions wherever the subject allows —
`noise_ceiling.svg` is drawn by calling `noise_ceiling_t`, and the hour-scan scatter is
drawn from the shipped audit artifact through the same loader the replay script uses.
A figure drawn by the code it illustrates cannot drift from it.

Deterministic: same inputs, byte-identical output, so a regenerated figure that differs
means something changed.

    python3 scripts/make_figures.py
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.replay_hour_scan import load_configs, pearson  # noqa: E402
from src.features.falsification.domain.services.multiplicity import (  # noqa: E402
    noise_ceiling_t,
    required_t,
)

OUT = REPO / "docs" / "figures"

W, H = 720, 420
PAD_L, PAD_R, PAD_T, PAD_B = 70, 28, 46, 56

INK = "#1a1a1a"
GRID = "#d8d8d8"
ACCENT = "#b3261e"
COOL = "#2a4b8d"
MUTED = "#6b6b6b"

STYLE = (
    "<style>"
    "text{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif}"
    ".ttl{font-size:15px;font-weight:600;fill:%s}"
    ".sub{font-size:11.5px;fill:%s}"
    ".ax{font-size:11px;fill:%s}"
    ".lbl{font-size:11px;fill:%s}"
    ".note{font-size:11px;fill:%s}"
    "</style>" % (INK, MUTED, MUTED, INK, MUTED)
)


class Canvas:
    """Minimal linear-scale SVG plotter. x may be linear or log10."""

    def __init__(self, title: str, subtitle: str, xlabel: str, ylabel: str,
                 xlim: tuple[float, float], ylim: tuple[float, float],
                 logx: bool = False) -> None:
        self.parts: list[str] = []
        self.title, self.subtitle = title, subtitle
        self.xlabel, self.ylabel = xlabel, ylabel
        self.logx = logx
        self.x0, self.x1 = (math.log10(xlim[0]), math.log10(xlim[1])) if logx else xlim
        self.y0, self.y1 = ylim

    def px(self, x: float) -> float:
        v = math.log10(x) if self.logx else x
        return PAD_L + (v - self.x0) / (self.x1 - self.x0) * (W - PAD_L - PAD_R)

    def py(self, y: float) -> float:
        return H - PAD_B - (y - self.y0) / (self.y1 - self.y0) * (H - PAD_T - PAD_B)

    def grid(self, xticks: list[float], yticks: list[float],
             xfmt=lambda v: f"{v:g}", yfmt=lambda v: f"{v:g}") -> None:
        for x in xticks:
            X = self.px(x)
            self.parts.append(
                f'<line x1="{X:.1f}" y1="{PAD_T}" x2="{X:.1f}" y2="{H-PAD_B}" '
                f'stroke="{GRID}" stroke-width="1"/>'
                f'<text class="ax" x="{X:.1f}" y="{H-PAD_B+18}" text-anchor="middle">{xfmt(x)}</text>'
            )
        for y in yticks:
            Y = self.py(y)
            self.parts.append(
                f'<line x1="{PAD_L}" y1="{Y:.1f}" x2="{W-PAD_R}" y2="{Y:.1f}" '
                f'stroke="{GRID}" stroke-width="1"/>'
                f'<text class="ax" x="{PAD_L-9}" y="{Y+4:.1f}" text-anchor="end">{yfmt(y)}</text>'
            )

    def hline(self, y: float, colour: str = INK, dash: str = "4,3", width: float = 1.2) -> None:
        Y = self.py(y)
        self.parts.append(
            f'<line x1="{PAD_L}" y1="{Y:.1f}" x2="{W-PAD_R}" y2="{Y:.1f}" '
            f'stroke="{colour}" stroke-width="{width}" stroke-dasharray="{dash}"/>'
        )

    def band(self, xa: float, xb: float, colour: str, opacity: float = 0.10) -> None:
        XA, XB = self.px(xa), self.px(xb)
        self.parts.append(
            f'<rect x="{XA:.1f}" y="{PAD_T}" width="{XB-XA:.1f}" height="{H-PAD_T-PAD_B}" '
            f'fill="{colour}" opacity="{opacity}"/>'
        )

    def path(self, pts: list[tuple[float, float]], colour: str, width: float = 2.0,
             dash: str | None = None) -> None:
        d = " ".join(("M" if i == 0 else "L") + f"{self.px(x):.1f},{self.py(y):.1f}"
                     for i, (x, y) in enumerate(pts))
        da = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(
            f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="{width}"{da}/>'
        )

    def dot(self, x: float, y: float, colour: str, r: float = 4.5) -> None:
        self.parts.append(
            f'<circle cx="{self.px(x):.1f}" cy="{self.py(y):.1f}" r="{r}" '
            f'fill="{colour}" stroke="#fff" stroke-width="1.4"/>'
        )

    def text(self, x: float, y: float, s: str, anchor: str = "start",
             cls: str = "lbl", dx: float = 0, dy: float = 0) -> None:
        self.parts.append(
            f'<text class="{cls}" x="{self.px(x)+dx:.1f}" y="{self.py(y)+dy:.1f}" '
            f'text-anchor="{anchor}">{s}</text>'
        )

    def note(self, s: str, row: int = 0) -> None:
        self.parts.append(
            f'<text class="note" x="{PAD_L}" y="{H-8-row*14}">{s}</text>'
        )

    def render(self) -> str:
        head = (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
            f'width="{W}" height="{H}" role="img">{STYLE}'
            f'<rect width="{W}" height="{H}" fill="#ffffff"/>'
            f'<text class="ttl" x="{PAD_L}" y="24">{self.title}</text>'
            f'<text class="sub" x="{PAD_L}" y="40">{self.subtitle}</text>'
        )
        axes = (
            f'<line x1="{PAD_L}" y1="{H-PAD_B}" x2="{W-PAD_R}" y2="{H-PAD_B}" '
            f'stroke="{INK}" stroke-width="1.3"/>'
            f'<line x1="{PAD_L}" y1="{PAD_T}" x2="{PAD_L}" y2="{H-PAD_B}" '
            f'stroke="{INK}" stroke-width="1.3"/>'
            f'<text class="ax" x="{(PAD_L+W-PAD_R)/2:.0f}" y="{H-PAD_B+38}" '
            f'text-anchor="middle">{self.xlabel}</text>'
            f'<text class="ax" x="16" y="{(PAD_T+H-PAD_B)/2:.0f}" text-anchor="middle" '
            f'transform="rotate(-90 16 {(PAD_T+H-PAD_B)/2:.0f})">{self.ylabel}</text>'
        )
        return head + "".join(self.parts) + axes + "</svg>"


def write(name: str, svg: str) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    path.write_text(svg, encoding="utf-8")
    return path


# ── 1. the noise ceiling ─────────────────────────────────────────────────────
def fig_noise_ceiling() -> Path:
    """sqrt(2 ln k) against search width, with the observed incidents on it.

    Drawn by calling the repository's own `noise_ceiling_t` and `required_t`, so the
    curve and the code cannot disagree.
    """
    c = Canvas(
        "The noise ceiling: what a search of width k produces from nothing",
        "sqrt(2 ln k) is the expected maximum of k standard normals. A winner sitting on it carries no evidence.",
        "k — number of specifications searched (log scale)",
        "|t|",
        (2, 3000), (1.0, 4.4), logx=True,
    )
    c.grid([2, 10, 100, 1000], [1.5, 2.0, 2.5, 3.0, 3.5, 4.0],
           xfmt=lambda v: f"{int(v)}", yfmt=lambda v: f"{v:.1f}")

    ks = [2 * 1.06 ** i for i in range(0, 130)]
    ks = [k for k in ks if k <= 3000]
    c.band(54, 80, ACCENT, 0.10)
    c.path([(k, noise_ceiling_t(int(round(k)))) for k in ks], ACCENT, 2.4)
    c.path([(k, required_t(int(round(k)))) for k in ks], COOL, 1.8, dash="5,4")
    c.hline(2.0, MUTED, "2,3", 1.0)
    c.text(2.4, 2.06, "floor |t| = 2", cls="lbl")

    c.dot(12, noise_ceiling_t(12), ACCENT)
    c.text(12, noise_ceiling_t(12), "k=12 &#8594; 2.23  (hour scan)", dx=9, dy=-7)
    c.dot(67, 2.92, INK, 5.5)
    c.text(67, 2.92, "observed 2.92 &#8212; 92-hypothesis search", dx=10, dy=-9)
    c.dot(1021, noise_ceiling_t(1021), ACCENT)
    c.text(1021, noise_ceiling_t(1021), "k=1021", dx=-6, dy=-9, anchor="end")

    c.text(2.4, 3.62, "&#8212; sqrt(2 ln k)  noise ceiling", cls="lbl")
    c.text(2.4, 3.42, "- - Bonferroni required |t|", cls="lbl")
    c.note("The shaded band is k = 54-80, where the ceiling is 2.82-2.96. The search's survivors landed at 2.92.")
    c.note("Nothing in the pass/fail logic objected: every candidate cleared its own nominal bar.", 1)
    return write("noise_ceiling.svg", c.render())


# ── 2. train vs test ─────────────────────────────────────────────────────────
def fig_train_vs_test() -> Path:
    """The 12 hour configurations, from the shipped audit artifact."""
    rows = load_configs()
    tr = [r["train"] for r in rows]
    te = [r["test"] for r in rows]
    rho = pearson(tr, te)
    picked = max(rows, key=lambda r: r["train"])
    rank = sorted(rows, key=lambda r: -r["test"]).index(picked) + 1

    c = Canvas(
        "Selecting on the training fold: 12 configurations, 12 losses",
        f"Pearson(train, test) = {rho:+.3f}. Ranking on train was close to ranking at random.",
        "train  avg_exit_return_pct", "test  avg_exit_return_pct",
        (-0.55, 0.20), (-1.35, 0.10),
    )
    c.grid([-0.5, -0.25, 0.0, 0.15], [-1.2, -0.9, -0.6, -0.3, 0.0],
           xfmt=lambda v: f"{v:+.2f}", yfmt=lambda v: f"{v:+.1f}")
    c.hline(0.0, INK, "3,3", 1.2)
    c.parts.append(
        f'<line x1="{c.px(0.0):.1f}" y1="{PAD_T}" x2="{c.px(0.0):.1f}" y2="{H-PAD_B}" '
        f'stroke="{INK}" stroke-width="1.2" stroke-dasharray="3,3"/>'
    )
    for r in rows:
        is_picked = r is picked
        c.dot(r["train"], r["test"], ACCENT if is_picked else COOL, 6.0 if is_picked else 4.2)
    c.text(picked["train"], picked["test"],
           f"selected on train (hours {picked['label']}) &#8212; rank {rank} of 12 on test",
           dx=-10, dy=16, anchor="end")
    c.note("Every configuration is below the horizontal zero line: all twelve lose money out of sample.")
    c.note("The positive train values are the spread of noise across twelve draws; its maximum is not a discovery.", 1)
    return write("train_vs_test_hour_scan.svg", c.render())


# ── 3. per-event vs session-clustered t ──────────────────────────────────────
def fig_clustering() -> Path:
    """Recorded reversals when the same data is clustered on sessions.

    Constants only — every pair below is already published in GRAVEYARD.md and
    METHODS.md. Nothing is recomputed here, so the figure cannot drift from the record.
    """
    pairs = [
        ("gap-down cell", 12.7, -0.92),
        ("panic x mid-cap", 7.4, 0.4),
        ("small-cap bounce", 13.0, -5.7),
    ]
    c = Canvas(
        "Effective sample size is the session count, not the trade count",
        "The same data, the same statistic — only the unit of independence changes.",
        "t-statistic", "",
        (-7.5, 14.5), (0.4, 3.7),
    )
    c.grid([-5, 0, 5, 10], [], xfmt=lambda v: f"{v:+g}")
    c.parts.append(
        f'<line x1="{c.px(0):.1f}" y1="{PAD_T}" x2="{c.px(0):.1f}" y2="{H-PAD_B}" '
        f'stroke="{INK}" stroke-width="1.2" stroke-dasharray="3,3"/>'
    )
    for i, (name, per_event, clustered) in enumerate(pairs):
        y = 3.1 - i * 0.9
        c.parts.append(
            f'<line x1="{c.px(per_event):.1f}" y1="{c.py(y):.1f}" '
            f'x2="{c.px(clustered):.1f}" y2="{c.py(y):.1f}" '
            f'stroke="{MUTED}" stroke-width="2"/>'
        )
        c.dot(per_event, y, COOL, 5.5)
        c.dot(clustered, y, ACCENT, 5.5)
        c.text(per_event, y, f"{per_event:+.1f}", dx=8, dy=4)
        c.text(clustered, y, f"{clustered:+.2f}", dx=-8, dy=4, anchor="end")
        c.text(-7.2, y + 0.3, name, cls="lbl")
    c.text(-7.2, 3.55, "&#9679; per-event t", cls="lbl")
    c.text(-2.6, 3.55, "&#9679; session-clustered t", cls="lbl")
    c.note("One panic morning produced 62 signals; one cell held 1,220 events over 304 sessions.")
    c.note("A per-event standard error is too small by roughly sqrt(n/S) before any other consideration.", 1)
    return write("per_event_vs_clustered_t.svg", c.render())


# ── 4. ceiling pricing ───────────────────────────────────────────────────────
def fig_ceiling_pricing() -> Path:
    """Net return to the ceiling is determined by the entry premium.

    Curve from the identity with public constants; the measured cross-sectional fit is
    overlaid. Both are already stated in tests/test_market_arithmetic.py.
    """
    C, S, F = 29.5, 0.003, 0.38

    def net(e: float) -> float:
        return ((1 + C / 100) / ((1 + e / 100) * (1 + S)) - 1) * 100 - F

    c = Canvas(
        "Pricing under a price limit: the outcome is arithmetic, not a forecast",
        "Once the entry premium is known, the return to the ceiling is determined. R-squared = 0.996 over 298 trades.",
        "entry premium e  (% over previous close)", "net return to the ceiling  (%)",
        (9.0, 33.0), (-4.0, 20.0),
    )
    c.grid([10, 15, 20, 25, 30], [-2.5, 0, 5, 10, 15],
           xfmt=lambda v: f"+{int(v)}%", yfmt=lambda v: f"{v:+g}")
    c.hline(0.0, INK, "3,3", 1.2)

    es = [9.0 + 0.25 * i for i in range(97)]
    c.path([(e, net(e)) for e in es], ACCENT, 2.4)
    c.path([(e, 26.18 - 0.924 * e) for e in es], COOL, 1.8, dash="6,4")

    for e in (10.0, 15.0, 20.0, 25.0):
        c.dot(e, net(e), ACCENT, 4.0)
    c.dot(29.0, net(29.0), INK, 5.5)
    c.text(29.0, net(29.0), "break-even &#8776; +29%", dx=-8, dy=-10, anchor="end")

    c.text(10.2, 18.4, "&#8212; identity  net(e) = (1+c)/((1+e)(1+s)) - 1 - f", cls="lbl")
    c.text(10.2, 16.8, "- - measured  net = 26.18 - 0.924 e   (R-squared 0.996)", cls="lbl")
    c.note("Raising the limit-up lock rate from 7.7% to 77.5% by selection left every band net-negative:")
    c.note("the selection improved a quantity that was not the one being paid for.", 1)
    return write("ceiling_pricing.svg", c.render())


# ── 5. kill-line shape ───────────────────────────────────────────────────────
def fig_kill_line_shape() -> Path:
    """The sigma/sqrt(n) shape, in normalised units.

    Alpha-safe by construction: mu = 0, sigma = 1, an illustrative quantile, and no
    numeric axis labels. Nothing real enters this function, which is why it can be
    published while the actual thresholds cannot.
    """
    z = 1.6

    def line(n: float) -> float:
        return -z / math.sqrt(n)

    c = Canvas(
        "Kill lines are sigma-over-root-n curves — shape published, values withheld",
        "An early sample may wander far below the mean before it means anything. A flat threshold cannot express that.",
        "forward sample size n  &#8594;", "threshold, normalised units  &#8594;",
        (4, 130), (-0.95, 0.12), logx=True,
    )
    for x in (5, 10, 30, 100):
        X = c.px(x)
        c.parts.append(f'<line x1="{X:.1f}" y1="{PAD_T}" x2="{X:.1f}" y2="{H-PAD_B}" '
                       f'stroke="{GRID}" stroke-width="1"/>')
    c.hline(0.0, INK, "3,3", 1.2)
    c.text(4.3, 0.03, "pre-freeze mean", cls="lbl")

    ns = [4 + 0.6 * i for i in range(215)]
    c.path([(n, line(n)) for n in ns], ACCENT, 2.4)
    for n in (5, 12, 40, 120):
        c.dot(n, line(n), INK, 5.0)

    c.note("Four pre-registered sample sizes are marked. Their positions and the axis scale are deliberately unlabelled:")
    c.note("thresholds indexed by n ARE this curve, and two points plus a published mean recover the whole distribution.", 1)
    c.note("Kill lines only. No confirm line exists anywhere, by construction. See docs/DISCLOSURE.md", 2)
    return write("kill_line_shape.svg", c.render())


FIGURES = (
    fig_noise_ceiling,
    fig_train_vs_test,
    fig_clustering,
    fig_ceiling_pricing,
    fig_kill_line_shape,
)


def main() -> None:
    for build in FIGURES:
        path = build()
        print(f"  {path.relative_to(REPO)}  ({path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
