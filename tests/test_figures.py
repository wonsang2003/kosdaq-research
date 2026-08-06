"""Figures, and the leak guard that runs with them.

The figures are committed rather than generated on demand, so they can go stale
against the code they illustrate. These tests regenerate them and compare, which turns
"the chart says 2.92" into an assertion rather than a caption.

The second half is the part that matters more. MODELS.md and the figures are the two
most math-dense artifacts in the repository and therefore the two most likely places
for a withheld number to appear while nobody is looking at the disclosure policy. The
guard runs here so it cannot be forgotten.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
FIGURES = REPO / "docs" / "figures"
SVG_NS = "{http://www.w3.org/2000/svg}"

EXPECTED = (
    "noise_ceiling.svg",
    "train_vs_test_hour_scan.svg",
    "per_event_vs_clustered_t.svg",
    "ceiling_pricing.svg",
    "kill_line_shape.svg",
)


@pytest.fixture(scope="module")
def regenerated() -> dict[str, str]:
    """Run the generator and return what it wrote.

    Runs the real script as a subprocess rather than importing it, so the test also
    covers the `python3 scripts/make_figures.py` path a reader would actually type.
    """
    result = subprocess.run(
        [sys.executable, "scripts/make_figures.py"],
        cwd=REPO, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"generator failed:\n{result.stderr}"
    return {name: (FIGURES / name).read_text(encoding="utf-8") for name in EXPECTED}


# ── the figures exist, are valid, and are deterministic ──────────────────────
def test_all_five_figures_are_generated(regenerated):
    for name in EXPECTED:
        assert (FIGURES / name).exists(), name


def test_every_figure_is_valid_svg(regenerated):
    for name, svg in regenerated.items():
        root = ET.fromstring(svg)
        assert root.tag == f"{SVG_NS}svg", name


def test_generation_is_deterministic(regenerated):
    """A regenerated figure that differs means something changed.

    Without this the committed SVGs are decoration; with it they are a snapshot test
    of the functions that draw them.
    """
    again = subprocess.run(
        [sys.executable, "scripts/make_figures.py"],
        cwd=REPO, capture_output=True, text=True, timeout=120,
    )
    assert again.returncode == 0
    for name, first in regenerated.items():
        assert (FIGURES / name).read_text(encoding="utf-8") == first, name


def test_nothing_is_drawn_outside_the_canvas(regenerated):
    """Catches the silent failure mode of a hand-rolled plotter: a point that is
    computed correctly and rendered off-screen."""
    for name, svg in regenerated.items():
        root = ET.fromstring(svg)
        width, height = float(root.get("width")), float(root.get("height"))
        for circle in root.iter(f"{SVG_NS}circle"):
            cx, cy = float(circle.get("cx")), float(circle.get("cy"))
            assert 0 <= cx <= width and 0 <= cy <= height, f"{name}: ({cx}, {cy})"


# ── the figures say what the documents say ───────────────────────────────────
def test_the_noise_ceiling_figure_carries_the_observed_value(regenerated):
    """2.92 is the number the whole figure exists to place on the curve."""
    assert "2.92" in regenerated["noise_ceiling.svg"]


def test_the_noise_ceiling_curve_is_drawn_by_the_repository_s_own_function(regenerated):
    """The curve and the implementation cannot disagree, because they are the same
    call. Verified by checking a plotted point against the function directly."""
    from src.features.falsification.domain.services.multiplicity import noise_ceiling_t

    assert noise_ceiling_t(12) == pytest.approx(2.23, abs=0.01)
    assert "2.23" in regenerated["noise_ceiling.svg"]


def test_the_hour_scan_figure_matches_the_shipped_audit_artifact(regenerated):
    """The scatter is drawn from data/audit/, so the postmortem, the replay command,
    and the figure are three views of one file."""
    from scripts.replay_hour_scan import load_configs, pearson

    rows = load_configs()
    rho = pearson([r["train"] for r in rows], [r["test"] for r in rows])
    assert f"{rho:+.3f}" in regenerated["train_vs_test_hour_scan.svg"]
    assert all(r["test"] < 0 for r in rows)


# ── the leak guard ───────────────────────────────────────────────────────────
# The blocklist of withheld values and names lives OUTSIDE this repository.
#
# It used to live here, as regex literals, and that was the largest leak in the
# project. A test asserting that a particular sample size must not appear publishes
# that sample size; one asserting a mechanism name must not appear publishes the
# mechanism. Care cannot fix that — the structure has to change. Hashing does not
# fix it either: these tokens are low-entropy, and a four-digit number is ten
# thousand guesses.
#
# So the list is read from a file the repository never contains. Without it the
# check cannot run, and the test below fails rather than skipping so that the lost
# coverage is visible — except where the absence is expected and declared.
BLOCKLIST_PATH = Path(
    os.environ.get(
        "KOSDAQ_REDACTION_BLOCKLIST",
        Path.home() / ".config" / "kosdaq" / "redaction-blocklist.txt",
    )
)


def _blocklist() -> list[str]:
    if not BLOCKLIST_PATH.exists():
        return []
    return [
        line.strip()
        for line in BLOCKLIST_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_the_blocklist_is_reachable_in_this_environment():
    """Guard against a silent skip, same shape as the production-file guard.

    A redaction check that quietly does nothing is worse than no check, because it
    reports green. CI sets ALLOW_MISSING_BLOCKLIST because the list is deliberately
    not in the clone; anywhere else, its absence is a failure.
    """
    if os.environ.get("ALLOW_MISSING_BLOCKLIST") == "1":
        pytest.skip("explicitly permitted to run without the redaction blocklist")
    assert BLOCKLIST_PATH.exists(), (
        f"redaction blocklist not found at {BLOCKLIST_PATH}; the withheld-value check "
        "is not running. Set ALLOW_MISSING_BLOCKLIST=1 if this is a clean-clone review."
    )


def test_no_withheld_value_appears_anywhere_in_the_tree():
    """The disclosure policy, enforced across the whole repository.

    Scoped to every tracked text file rather than to the documents. The first
    version of this check looked only at prose, and the values it was meant to
    protect were sitting in a test file, a docstring constant and a JSON key.
    """
    patterns = _blocklist()
    if not patterns:
        pytest.skip(f"no blocklist at {BLOCKLIST_PATH}")

    offenders = []
    for path in sorted(REPO.rglob("*")):
        if not path.is_file() or any(part.startswith(".") for part in path.relative_to(REPO).parts):
            continue
        if path.suffix in {".parquet", ".png", ".pyc"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern in patterns:
            if re.search(pattern, text):
                offenders.append(f"{path.relative_to(REPO)} matches a withheld pattern")
    assert offenders == [], offenders


# Shape guards, kept inline because they spell nothing out. Several live cells and
# features are named after their own thresholds, so pasting a cell label publishes
# the rule it encodes; these match the *form* such a label takes.
SHAPES = {
    "threshold welded to a take-profit label": r"\bTP\s*@?\s*\d|\btp_?\d",
    "parameter welded to a feature name": r"_(?:slope|imb|gap|prem|band|thr)\d",
    "cell label carrying its own level": r"\b(?:cell|band|bucket)_[-+]?\d",
}

GUARDED = (
    "MODELS.md", "METHODS.md", "GRAVEYARD.md", "README.md",
    "docs/postmortems/01-lock-fill-fantasy.md",
    "docs/postmortems/04-train-selection-hour-scan.md",
    "docs/survivors/01-event-driven-long.md",
) + tuple(f"docs/figures/{n}" for n in EXPECTED)


@pytest.mark.parametrize(("label", "pattern"), sorted(SHAPES.items()))
def test_no_threshold_encoding_name_appears(label, pattern):
    offenders = [
        relative for relative in GUARDED
        if re.search(pattern, (REPO / relative).read_text(encoding="utf-8"))
    ]
    assert not offenders, f"{label} appears in {offenders}"


def test_the_kill_line_figure_has_no_numeric_axis_labels():
    """Alpha-safe by construction rather than by care.

    The shape of a sigma/sqrt(n) curve is publishable; the four thresholds are not,
    because two points plus a published mean recover the return distribution. A
    numbered axis would leak the second while illustrating the first.
    """
    root = ET.fromstring((FIGURES / "kill_line_shape.svg").read_text(encoding="utf-8"))
    numeric = [
        el.text for el in root.iter(f"{SVG_NS}text")
        if el.text and re.fullmatch(r"\s*[-+−]?\d+(\.\d+)?%?\s*", el.text)
    ]
    assert numeric == [], f"numeric axis labels present: {numeric}"


# ── MODELS.md integrity ──────────────────────────────────────────────────────
def test_every_path_models_md_references_exists():
    """A models document that links to code which has moved is a document that has
    stopped being checkable. Same pattern as test_audit_artifacts."""
    text = (REPO / "MODELS.md").read_text(encoding="utf-8")
    referenced = {
        target for target in re.findall(r"\]\((?!http)([^)#]+?)\)", text)
        if target.endswith((".py", ".json", ".md", ".svg"))
    }
    assert referenced, "guard: MODELS.md should reference implementation paths"
    missing = sorted(t for t in referenced if not (REPO / t).exists())
    assert missing == [], f"MODELS.md links to missing paths: {missing}"


def test_models_md_carries_no_latex_in_headings():
    """GitHub renders math unreliably inside headings; a formula there shows as raw
    source on the page a reviewer actually lands on."""
    offenders = [
        line for line in (REPO / "MODELS.md").read_text(encoding="utf-8").splitlines()
        if line.startswith("#") and "$" in line
    ]
    assert offenders == [], offenders


def test_every_model_entry_declares_its_status():
    """`implemented here` vs `quoted from the research record`.

    The tag is what stops the document implying an implementation that is not present,
    which would be the same category of claim this repository documents elsewhere.
    """
    text = (REPO / "MODELS.md").read_text(encoding="utf-8")
    entries = re.findall(r"^## (?!Notation)(.+)$", text, re.M)
    statuses = re.findall(r"^\*\*Status:\*\*", text, re.M)
    assert len(statuses) >= len(entries) - 1, (
        f"{len(entries)} entries but only {len(statuses)} status tags"
    )
