"""The documents, checked the way the code is checked.

This repository's argument is that a claim without a reproduction is an assertion.
That applies to its own prose: a cross-reference that no longer resolves, or a count
in the README that has drifted from the suite, is the same defect as a stale test —
it reports a state of the world that stopped being true and nothing objects.

These tests object.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

def _is_documentation(path: Path) -> bool:
    """Exclude everything under a dot-directory.

    An earlier version named `.venv` and `.git` specifically and missed
    `.pytest_cache/README.md`, which exists on a developer's machine and not in a
    clean clone. The parametrised tests below then produced two more test cases
    locally than in CI, and the README's advertised test count — asserted further
    down this file — was right on one machine and wrong on the other.

    A count that depends on working-directory state is not a check, it is noise.
    The rule is therefore structural rather than a list of names: if any path
    component starts with a dot, it is tooling, not documentation.
    """
    return not any(part.startswith(".") for part in path.relative_to(REPO).parts)


MARKDOWN = sorted(p for p in REPO.rglob("*.md") if _is_documentation(p))

LINK = re.compile(r"\]\((?!https?:|mailto:)([^)]+)\)")


def _anchors(markdown: Path) -> set[str]:
    """GitHub's slug rule, restricted to what this repository actually uses.

    Lowercase, strip punctuation, spaces to hyphens. Headings here contain no
    duplicates, so the `-1` disambiguation suffix never arises.
    """
    found = set()
    for line in markdown.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            text = re.sub(r"^#+\s*", "", line).strip().lower()
            found.add(re.sub(r"[^\w\s-]", "", text).replace(" ", "-"))
    return found


def test_the_markdown_corpus_is_where_it_is_expected():
    """Guard: if this collapses to a handful of files the two tests below pass
    vacuously and stop protecting anything."""
    assert len(MARKDOWN) >= 10, [str(p.relative_to(REPO)) for p in MARKDOWN]


@pytest.mark.parametrize("markdown", MARKDOWN, ids=lambda p: p.name)
def test_every_relative_link_resolves(markdown: Path):
    broken = []
    for target in LINK.findall(markdown.read_text(encoding="utf-8")):
        path, _, _anchor = target.partition("#")
        if path and not (markdown.parent / path).resolve().exists():
            broken.append(target)
    assert broken == [], f"{markdown.relative_to(REPO)} links to missing: {broken}"


# GitHub renders math with KaTeX under a stricter configuration than stock KaTeX:
# anything that defines or expands a macro, and anything that emits raw HTML, is
# refused outright. A document using one of these renders as a red error box on the
# page while looking perfectly correct in every local previewer.
GITHUB_REFUSES = (
    "operatorname", "def", "newcommand", "renewcommand", "gdef", "edef", "xdef",
    "let", "futurelet", "global", "newextarrow", "includegraphics",
    "htmlClass", "htmlId", "htmlStyle", "htmlData",
)

DISPLAY_MATH = re.compile(r"\$\$(.+?)\$\$", re.S)
INLINE_MATH = re.compile(r"(?<!\$)\$(?!\$)([^\n$]*\\[^\n$]*)\$(?!\$)")


def _math_spans(text: str) -> list[str]:
    """Every LaTeX span in a document.

    Inline spans must contain a backslash to count. Without that condition a line
    like "US$700k ... US$7,000" reads as one inline span of prose — GitHub does not
    treat it as math, and neither should this.
    """
    return DISPLAY_MATH.findall(text) + INLINE_MATH.findall(text)


@pytest.mark.parametrize("markdown", MARKDOWN, ids=lambda p: p.name)
def test_math_uses_only_what_github_will_render(markdown: Path):
    """Caught in production: MODELS.md rendered two error boxes on GitHub.

    `\\operatorname{Corr}` is refused by GitHub's macro policy, and `\\#` loses its
    backslash to the markdown escape pass before KaTeX sees it, leaving a bare `#`
    that KaTeX reads as a macro parameter. Both render fine locally, which is exactly
    why they shipped.

    This check is static rather than a real KaTeX run — the suite has no Node
    dependency and should not grow one. The full render was done once, out of band,
    against katex 0.18.1 over all 201 spans in this repository; this test pins the
    two failure modes that reached a reader.
    """
    offenders = []
    for span in _math_spans(markdown.read_text(encoding="utf-8")):
        for macro in GITHUB_REFUSES:
            if re.search(rf"\\{macro}\b", span):
                offenders.append(f"\\{macro} in: {span.strip()[:60]}")
        if re.search(r"(?:^|[^\\])#", span):
            offenders.append(f"bare # in: {span.strip()[:60]}")
        if re.search(r"\\#", span):
            offenders.append(f"escaped \\# (markdown eats the backslash) in: {span.strip()[:60]}")
    assert offenders == [], f"{markdown.relative_to(REPO)}: {offenders}"


@pytest.mark.parametrize("markdown", MARKDOWN, ids=lambda p: p.name)
def test_every_heading_anchor_resolves(markdown: Path):
    """The failure mode a plain link check misses.

    A renamed heading leaves every deep link pointing at the top of the right file,
    which looks fine and silently drops the reader somewhere else.
    """
    broken = []
    for target in LINK.findall(markdown.read_text(encoding="utf-8")):
        path, _, anchor = target.partition("#")
        if not anchor:
            continue
        destination = (markdown.parent / path).resolve() if path else markdown
        if destination.suffix == ".md" and destination.exists():
            if anchor not in _anchors(destination):
                broken.append(target)
    assert broken == [], f"{markdown.relative_to(REPO)} anchors missing: {broken}"


def test_the_graveyard_intro_counts_its_own_classes():
    """The taxonomy grew; the sentence describing it has to grow with it.

    Seven original classes plus the extensions added afterwards. If a class is added
    and the intro is not updated, the document misstates its own history — the exact
    kind of drift the record exists to prevent.
    """
    text = (REPO / "GRAVEYARD.md").read_text(encoding="utf-8")
    classes = re.findall(r"^## (\d+)\.", text, re.M)
    assert [int(n) for n in classes] == list(range(1, len(classes) + 1)), classes

    extensions = len(re.findall(r"^## \d+\..*\*\(extension\)\*", text, re.M))
    assert len(classes) == 7 + extensions, (
        f"{len(classes)} classes and {extensions} marked extensions do not reconcile"
    )
    written = {3: "three", 4: "four", 5: "five"}[extensions]
    assert f"{written} were added later" in text, (
        f"intro should say '{written} were added later' for {extensions} extensions"
    )


def test_the_cancelled_section_matches_the_verdict_table():
    text = (REPO / "GRAVEYARD.md").read_text(encoding="utf-8")
    section = text.split("## Cancelled before deployment")[1].split("\n## ")[0]
    bullets = re.findall(r"^- \*\*", section, re.M)
    stated = re.search(r"\| Cancelled before deployment \| (\d+)", text)
    assert stated, "verdict table lost its cancelled-before-deployment row"
    assert len(bullets) == int(stated.group(1)), (
        f"{len(bullets)} entries listed, {stated.group(1)} claimed"
    )


def test_every_pinned_dependency_is_actually_imported():
    """A pin nothing imports is a claim with no evidence behind it.

    Found one: `scikit-learn` was pinned and never imported anywhere in the tree. It
    cost a reviewer an install, and it widened the surface on which `make setup` could
    fail, in exchange for nothing. The same idiom as the empty packages deleted earlier
    — if it is listed, something must use it.
    """
    requirements = (REPO / "requirements.txt").read_text(encoding="utf-8")
    pinned = [
        line.split("==")[0].strip()
        for line in requirements.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert pinned, "guard: requirements.txt should pin something"

    # Distribution name -> the name you actually import.
    module_of = {"python-ulid": "ulid", "scikit-learn": "sklearn"}

    # Backends reached through another library rather than imported. Each needs a
    # reason and a witness — the string that proves the backend is really exercised —
    # so this dict cannot become a place to park pins that are simply unused.
    indirect = {"pyarrow": ("pandas' parquet engine", "parquet")}

    sources = "\n".join(
        p.read_text(encoding="utf-8")
        for p in list(REPO.rglob("*.py"))
        if ".venv" not in p.parts
    )

    unused = []
    for name in pinned:
        if name in indirect:
            _reason, witness = indirect[name]
            assert witness in sources, f"{name} exempted but {witness!r} never appears"
            continue
        module = module_of.get(name, name)
        if not re.search(rf"\b(?:import|from)\s+{re.escape(module)}\b", sources):
            unused.append(name)
    assert unused == [], f"pinned but never imported: {unused}"


def test_the_readme_test_count_is_the_actual_test_count():
    """The number a reviewer reads first, made self-verifying.

    A README that advertises a count it no longer has is a small lie about a checkable
    fact, sitting in a document whose entire claim is that its facts are checkable.
    """
    collected = subprocess.run(
        # `-o addopts=` resets pytest.ini's own -q; without it the flag doubles and
        # pytest prints per-file counts instead of the total.
        [sys.executable, "-m", "pytest", "-o", "addopts=", "--collect-only", "-q"],
        cwd=REPO, capture_output=True, text=True, timeout=600,
    )
    match = re.search(r"(\d+) tests? collected", collected.stdout)
    assert match, collected.stdout[-2000:]
    actual = int(match.group(1))

    claimed = set(
        int(n) for n in
        re.findall(r"(\d{3,4}) tests", (REPO / "README.md").read_text(encoding="utf-8"))
    )
    assert claimed, "guard: the README should state a test count"
    assert claimed == {actual}, f"README claims {sorted(claimed)}, suite has {actual}"
