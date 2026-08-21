"""
Shipped-documentation integrity tests.

WHY THIS FILE EXISTS
--------------------
On 2026-07-28 an edit to PROJECT_DOCUMENTATION.md opened a `<details>` block
and never closed it. In every GitHub-flavoured Markdown renderer an unclosed
`<details>` swallows *everything after it* into a collapsed disclosure widget --
in that instance sections 7.4 through 18 of the customer-facing documentation
silently disappeared from the rendered page. The file still "looked fine" as
plain text, `git diff` looked fine, and the release builder happily packaged it.

Documentation is a shipped artifact of this product, so it gets the same
treatment as code: an automated guard rather than a promise to be careful.

These tests fail if a shipped Markdown document has:
  * unbalanced <details> / <summary> HTML,
  * an unterminated fenced code block,
  * a relative link to a Markdown file that does not exist,
  * an unresolved authoring placeholder (TODO / TBD / FIXME / lorem ipsum).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Documents that are delivered to the customer. Internal dated dev-history
# reports are deliberately excluded: they are frozen provenance, not live docs.
SHIPPED_DOCS = [
    "PROJECT_DOCUMENTATION.md",
    "README.md",
    "DEPLOY.md",
    "docs/CSP_HARDENING_OPTION_B.md",
    "docs/PERFORMANCE_REPORT.md",
    "docs/SECURITY_PENTEST_REPORT.md",
]


def _existing_docs() -> list[Path]:
    out: list[Path] = []
    for name in SHIPPED_DOCS:
        p = REPO_ROOT / name
        if p.is_file():
            out.append(p)
    return out


def _doc_ids(paths: list[Path]) -> list[str]:
    return [p.relative_to(REPO_ROOT).as_posix() for p in paths]


DOCS = _existing_docs()


def test_shipped_docs_are_present():
    """The core customer-facing documents must exist at all."""
    missing = [
        n
        for n in ("PROJECT_DOCUMENTATION.md", "README.md", "DEPLOY.md")
        if not (REPO_ROOT / n).is_file()
    ]
    assert not missing, f"shipped documentation missing: {missing}"


@pytest.mark.parametrize("doc", DOCS, ids=_doc_ids(DOCS))
def test_html_disclosure_tags_are_balanced(doc: Path):
    """
    An unclosed <details> hides every following section in rendered Markdown.
    This is the exact 2026-07-28 defect this file was created for.
    """
    text = doc.read_text(encoding="utf-8")
    for tag in ("details", "summary"):
        opens = len(re.findall(rf"<{tag}(?:\s[^>]*)?>", text))
        closes = len(re.findall(rf"</{tag}\s*>", text))
        assert opens == closes, (
            f"{doc.name}: unbalanced <{tag}> -- {opens} open vs {closes} close. "
            f"An unclosed <{tag}> collapses all following content when rendered."
        )


@pytest.mark.parametrize("doc", DOCS, ids=_doc_ids(DOCS))
def test_fenced_code_blocks_are_closed(doc: Path):
    """An unterminated ``` fence renders the rest of the document as code."""
    fences = re.findall(r"^\s*```", doc.read_text(encoding="utf-8"), re.M)
    assert len(fences) % 2 == 0, (
        f"{doc.name}: {len(fences)} code-fence markers (odd) -- "
        "one fenced block is never closed."
    )


@pytest.mark.parametrize("doc", DOCS, ids=_doc_ids(DOCS))
def test_relative_markdown_links_resolve(doc: Path):
    """
    A shipped doc must not link to a Markdown file that is not in the package.
    The release builder relocates several dev-history reports into
    docs/project-history/, so stale links are a live failure mode.
    """
    text = doc.read_text(encoding="utf-8")
    broken: list[str] = []
    for target in re.findall(r"\]\(([A-Za-z0-9_./-]+\.md)\)", text):
        if target.startswith(("http:", "https:", "/")):
            continue
        if not (doc.parent / target).resolve().is_file():
            broken.append(target)
    assert not broken, f"{doc.name}: broken relative links -> {sorted(set(broken))}"


@pytest.mark.parametrize("doc", DOCS, ids=_doc_ids(DOCS))
def test_no_unresolved_authoring_placeholders(doc: Path):
    """Customer-facing docs must not ship TODO/TBD/FIXME/lorem-ipsum stubs."""
    offenders: list[str] = []
    for lineno, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
        # `TODO` inside a backticked span is usually describing code, not a stub.
        stripped = re.sub(r"`[^`]*`", "", line)
        if re.search(r"\b(TODO|TBD|FIXME|XXX_PLACEHOLDER|lorem ipsum)\b", stripped, re.I):
            offenders.append(f"{doc.name}:{lineno}: {line.strip()[:90]}")
    assert not offenders, "unresolved placeholders:\n" + "\n".join(offenders)


def test_documentation_covers_every_numbered_section():
    """
    PROJECT_DOCUMENTATION.md advertises an 18-section structure. Verify the
    numbered `## N.` headings actually run 1..18 with no gaps, so a section
    cannot be lost by an editing accident without the build noticing.
    """
    doc = REPO_ROOT / "PROJECT_DOCUMENTATION.md"
    text = doc.read_text(encoding="utf-8")
    found = sorted(int(m) for m in re.findall(r"^##\s+(\d+)\.\s", text, re.M))
    assert found, "no numbered '## N.' sections found in PROJECT_DOCUMENTATION.md"
    expected = list(range(1, max(found) + 1))
    assert found == expected, (
        f"section numbering has gaps/duplicates: found {found}, expected {expected}"
    )
    assert max(found) >= 18, (
        f"documentation shrank: highest section is {max(found)}, expected >= 18"
    )


def test_documented_model_column_counts_match_the_code():
    """
    The data-model table in section 5.1 quotes a column count per model. Every
    one of those numbers was found WRONG on 2026-07-28 (65/34/27/12/16 quoted
    versus 63/30/26/9/12 actual) because they were transcribed by hand and the
    schema moved on. Hand-maintained counts in documentation rot silently, so
    they are now verified against the mapped SQLAlchemy tables instead.
    """
    import app.models  # noqa: F401  -- register every mapper
    from app.models.candidate import Candidate, CandidateAssignment
    from app.models.client import Client
    from app.models.demand import Demand, JobCategory

    models = {
        "Candidate": Candidate,
        "Demand": Demand,
        "Client": Client,
        "CandidateAssignment": CandidateAssignment,
        "JobCategory": JobCategory,
    }

    text = (REPO_ROOT / "PROJECT_DOCUMENTATION.md").read_text(encoding="utf-8")
    wrong: list[str] = []
    checked = 0
    for name, model in models.items():
        # Match the table row:  | `Candidate` | 63 | ... |
        m = re.search(rf"^\|\s*`{name}`\s*\|\s*(\d+)\s*\|", text, re.M)
        if not m:
            wrong.append(f"{name}: no documented row found in section 5.1")
            continue
        checked += 1
        documented = int(m.group(1))
        actual = len(model.__table__.columns)
        if documented != actual:
            wrong.append(f"{name}: documented {documented}, actual {actual}")

    assert not wrong, "section 5.1 column counts are stale:\n  " + "\n  ".join(wrong)
    assert checked == len(models), "expected a documented row for every core model"


def test_documented_table_count_matches_the_code():
    """Section 5 opens with a total table count; keep it honest too."""
    import app.models  # noqa: F401
    from app.db.session import Base

    text = (REPO_ROOT / "PROJECT_DOCUMENTATION.md").read_text(encoding="utf-8")
    m = re.search(r"\*\*(\d+)\s+tables\.\*\*", text)
    assert m, "section 5 no longer states a '**N tables.**' total"
    documented, actual = int(m.group(1)), len(Base.metadata.tables)
    assert documented == actual, (
        f"documented table count {documented} != actual {actual}"
    )


def test_version_string_is_consistent_everywhere():
    """The package version must agree in code, docs and the release message.

    Caught on 2026-07-29: functional changes had shipped (a new Alembic
    migration, the candidates sort-key work, 40 extra tests) but every
    in-package version string still said 1.0.0 while the release ZIP was being
    named v1.1.0. A version that disagrees with itself makes support
    impossible -- an operator reporting "1.0.0" from /api/health could be
    running either build. This pins them together.
    """
    from app.core.config import settings

    version = settings.APP_VERSION
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), (
        f"APP_VERSION {version!r} is not a three-part semantic version"
    )

    doc = (REPO_ROOT / "PROJECT_DOCUMENTATION.md").read_text(encoding="utf-8")

    # The header fact table.
    m = re.search(r"^\|\s*\*\*Version\*\*\s*\|\s*([0-9.]+)\s*\|", doc, re.M)
    assert m, "PROJECT_DOCUMENTATION.md no longer states a **Version** row"
    assert m.group(1) == version, (
        f"documentation header says {m.group(1)}, APP_VERSION is {version}"
    )

    # The APP_VERSION default advertised in the configuration reference.
    m = re.search(r"\|\s*`APP_VERSION`\s*\|\s*([0-9.]+)\s*\|", doc)
    assert m, "the configuration table no longer documents APP_VERSION"
    assert m.group(1) == version, (
        f"config table documents APP_VERSION={m.group(1)}, code says {version}"
    )

    # The release commit message titles the build the ZIP ships as.
    msg = (REPO_ROOT / "scripts" / "release_commit_message.txt").read_text(
        encoding="utf-8"
    )
    m = re.match(r"Dogar Portal v([0-9.]+)\b", msg)
    assert m, "release_commit_message.txt must open with 'Dogar Portal vX.Y.Z'"
    assert m.group(1) == version, (
        f"release message says v{m.group(1)}, APP_VERSION is {version}"
    )

    # No shipped doc may still reference a *different* version of this product.
    stale: list[str] = []
    for rel in SHIPPED_DOCS + ["scripts/release_commit_message.txt"]:
        path = REPO_ROOT / rel
        if not path.exists():
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for found in re.findall(r"(?:dogar[-_ ]portal[-_ ]?v?|version\"?:?\s*\"?)"
                                    r"(\d+\.\d+\.\d+)", line, re.I):
                if found != version:
                    stale.append(f"{rel}:{n}: refers to {found}: {line.strip()}")
    assert not stale, (
        f"shipped docs still reference a version other than {version}:\n  "
        + "\n  ".join(stale)
    )


def test_documented_test_counts_match_the_suite():
    """Keep the advertised test counts honest.

    These numbers have gone stale three times in this project's life (228/194,
    then 271/217, then 273) because every change to the suite has to be
    remembered in three separate prose locations. Counting them here removes
    the remembering.

    The test-function and module counts are derived by parsing the test files
    (an AST walk), which is exact. The *executed case* count is deliberately
    only bounded, not pinned: reproducing pytest's parametrise expansion would
    mean running collection from inside a collected test, which is fragile for
    no real gain. The invariant that matters -- executed >= defined -- still
    catches a count that was never updated downward.
    """
    import ast

    test_files = sorted((REPO_ROOT / "tests").glob("test_*.py"))
    defined = 0
    for path in test_files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef)
            ) and node.name.startswith("test_"):
                defined += 1

    doc = (REPO_ROOT / "PROJECT_DOCUMENTATION.md").read_text(encoding="utf-8")
    m = re.search(
        r"\*\*(\d+)\s+test functions across (\d+) modules\*\*\s*\((\d+)\s+executed",
        doc,
    )
    assert m, "section 14 no longer states '**N test functions across M modules** (K executed'"
    doc_funcs, doc_modules, doc_cases = (int(g) for g in m.groups())

    assert doc_funcs == defined, (
        f"documentation claims {doc_funcs} test functions, the suite defines {defined}"
    )
    assert doc_modules == len(test_files), (
        f"documentation claims {doc_modules} test modules, "
        f"tests/ contains {len(test_files)}"
    )
    assert doc_cases >= defined, (
        f"documentation claims {doc_cases} executed cases, which is fewer than the "
        f"{defined} test functions defined -- the number is stale"
    )

    # The same figures are repeated in the directory map and the release notes;
    # a single source of truth in prose is not possible, so check every copy.
    m = re.search(r"├── tests/\s+# (\d+) test functions", doc)
    assert m, "the directory map no longer annotates tests/ with a function count"
    assert int(m.group(1)) == defined, (
        f"directory map says {m.group(1)} test functions, actual {defined}"
    )

    m = re.search(r"#\s*(\d+) tests", doc)
    assert m, "section 14 no longer shows a '# N tests' comment on the pytest command"
    assert int(m.group(1)) == doc_cases, (
        f"the pytest command comment says {m.group(1)} tests but section 14 "
        f"states {doc_cases} executed cases"
    )

    msg = (REPO_ROOT / "scripts" / "release_commit_message.txt").read_text(
        encoding="utf-8"
    )
    m = re.search(r"(\d+) automated tests passing", msg)
    assert m, "release_commit_message.txt no longer states 'N automated tests passing'"
    assert int(m.group(1)) == doc_cases, (
        f"release message says {m.group(1)} tests, documentation says {doc_cases}"
    )

    # START_HERE.md is the FIRST thing a deployer reads, and it stamps the suite
    # size in its header. It was not covered here until 2026-08-04, and it had
    # silently drifted (said 560 while the suite ran 586) -- which is the exact
    # failure mode this module exists to prevent. Every copy of the number is
    # now checked, so none can rot in isolation.
    start_here = (REPO_ROOT / "START_HERE.md").read_text(encoding="utf-8")
    m = re.search(r"Backend suite \*\*(\d+) passed / 0 failed\*\*", start_here)
    assert m, (
        "START_HERE.md no longer stamps the suite size as "
        "'Backend suite **N passed / 0 failed**'"
    )
    assert int(m.group(1)) == doc_cases, (
        f"START_HERE.md says {m.group(1)} tests passed, documentation says {doc_cases}"
    )


def test_documented_permission_count_matches_the_catalogue():
    """Every "<N> permission" claim in the shipped docs must equal the code.

    WHY: README.md said "48 permission keys across 11 modules" while
    PERMISSION_CATALOG had grown to 49, and PROJECT_DOCUMENTATION.md said 49.
    Two shipped documents disagreed with each other, and one disagreed with the
    code. Nobody noticed because nothing derived the number from the source.
    This test makes the catalogue the single source of truth for that figure.
    """
    from app.core.permissions import PERMISSION_CATALOG

    modules = len(PERMISSION_CATALOG)
    keys = sum(len(m["items"]) for m in PERMISSION_CATALOG)
    # Sanity-check the shape so a refactor of PERMISSION_CATALOG cannot make
    # this test silently count zero and pass.
    assert modules >= 5 and keys >= 20, (
        f"PERMISSION_CATALOG shape looks wrong: {modules} modules / {keys} keys"
    )

    pattern = re.compile(r"\*{0,2}(\d+)\*{0,2}\s+permission(?:s| keys)\b")
    checked = 0
    for path in _existing_docs():
        text = path.read_text(encoding="utf-8")
        for m in pattern.finditer(text):
            claimed = int(m.group(1))
            line = text[: m.start()].count("\n") + 1
            assert claimed == keys, (
                f"{path.name}:{line} claims {claimed} permissions but "
                f"PERMISSION_CATALOG defines {keys}"
            )
            checked += 1

    assert checked, (
        "no '<N> permissions' claim found in any shipped doc -- if the wording "
        "changed, update this test's regex or it silently guards nothing"
    )


def test_documented_module_count_matches_the_catalogue():
    """The permission "across N modules" figure must also come from the code.

    NOTE the regex deliberately requires the word "permission" BEFORE "across".
    A looser pattern matched "225 test functions across 28 modules" -- a
    sentence about *test* modules -- and demanded it equal the permission-module
    count. Two unrelated things in these docs are both called "modules", so the
    permission context has to be part of the match.
    """
    from app.core.permissions import PERMISSION_CATALOG

    modules = len(PERMISSION_CATALOG)
    pattern = re.compile(
        r"permission(?:s| keys)\b[^.\n]*?across\s+\*{0,2}(\d+)\*{0,2}\s+modules\b"
    )
    checked = 0
    for path in _existing_docs():
        text = path.read_text(encoding="utf-8")
        for m in pattern.finditer(text):
            claimed = int(m.group(1))
            line = text[: m.start()].count("\n") + 1
            assert claimed == modules, (
                f"{path.name}:{line} claims {claimed} permission modules but "
                f"PERMISSION_CATALOG defines {modules}"
            )
            checked += 1

    assert checked, "no 'across N modules' claim found -- regex may be stale"


def test_live_docs_only_name_asset_files_that_actually_ship():
    """Every `app/static/...` file path quoted in a *live* shipped document must
    exist on disk.

    WHY: the v1.1.0 external audit's Finding 4 was that the documentation named
    assets which no longer exist -- e.g.
    ``app/static/vendor/fontawesome/css/fontawesome.css``, which had moved to
    ``app/static/vendor/fonts/fontawesome.css``. A reader following such a path
    concludes the package is incomplete.

    The dated internal reports are deliberately NOT checked here: they are frozen
    provenance and several are literally *about* the filenames in question, so
    "fixing" their paths would falsify the record. They instead carry an explicit
    ARCHIVED banner (pinned by the test below).
    """
    # Only match paths that look like real file references: a static/ path ending
    # in a file extension. Prose like "app/static/" (a directory) is not a claim
    # about a file and is skipped.
    pattern = re.compile(r"app/static/[A-Za-z0-9_./-]+\.(?:css|js|jpg|jpeg|png|svg|ico|woff2?|ttf|html)")

    # A path may also be named in order to state that it was REMOVED. That is
    # the opposite of the defect this test exists to catch (a reader following
    # a dead path and concluding the package is incomplete) -- suppressing such
    # sentences would force the docs to describe the Jinja2 eradication without
    # naming the files it deleted. A retirement is recognised by the wording of
    # the sentence the reference sits in, not by a blanket per-file waiver, so a
    # genuinely stale "load app/static/x.js" claim elsewhere still fails.
    retirement = re.compile(
        r"\b(no longer ship|no longer ships|was removed|were removed|has been removed|"
        r"have been removed|removed in the|retired|previously described|used to|"
        r"does not ship|obsolete|superseded)\b",
        re.I,
    )

    # Sentence boundary = a period followed by whitespace/end. Splitting on a
    # bare "." would break inside the filename itself ("safe_dom.js"), cutting
    # the sentence in half and hiding the retirement wording that follows it.
    _boundary = re.compile(r"\.(?=\s|$)")

    def _sentence_around(text: str, idx: int) -> str:
        starts = [m.end() for m in _boundary.finditer(text, 0, idx)]
        para = text.rfind("\n\n", 0, idx)
        start = max([para] + starts) if (starts or para != -1) else 0
        m_end = _boundary.search(text, idx)
        end = m_end.end() if m_end else len(text)
        return text[start:end]

    missing = []
    checked = 0
    for path in _existing_docs():
        text = path.read_text(encoding="utf-8")
        for m in pattern.finditer(text):
            ref = m.group(0)
            checked += 1
            if not (REPO_ROOT / ref).exists():
                if retirement.search(_sentence_around(text, m.start())):
                    continue  # documented as removed, not offered as a live path
                line = text[: m.start()].count("\n") + 1
                missing.append(f"{path.name}:{line} names {ref!r} which does not ship")
    assert checked, "no app/static asset paths found in the live docs -- regex may be stale"
    assert not missing, (
        "live documentation points at assets that are not in the package:\n  - "
        + "\n  - ".join(missing)
    )


def test_dated_internal_reports_are_marked_archived():
    """Each dated dev-history report must carry the ARCHIVED banner.

    Those reports are kept for provenance but their paths, counts and version
    numbers are frozen at their date. Without an explicit banner a reader treats
    them as current reference material and follows stale paths -- which is exactly
    what the v1.1.0 audit did when it reported "documentation mentions obsolete
    asset names". The banner turns that from a defect into a stated property.

    The filename pattern (a YYYY-MM-DD stamp) is what makes a report "dated", so
    any *new* dated report is caught automatically.
    """
    dated = re.compile(r"\d{4}-\d{2}-\d{2}.*\.md$")
    candidates = sorted(
        p for p in list(REPO_ROOT.glob("*.md")) + list(REPO_ROOT.glob("docs/**/*.md"))
        if dated.search(p.name)
    )
    assert candidates, "no dated internal reports found -- glob may be stale"
    unmarked = [
        str(p.relative_to(REPO_ROOT))
        for p in candidates
        if "ARCHIVED — point-in-time record" not in p.read_text(encoding="utf-8")
    ]
    assert not unmarked, (
        "dated internal reports are missing the ARCHIVED banner, so readers will "
        "mistake their frozen paths/counts for current fact:\n  - " + "\n  - ".join(unmarked)
    )
