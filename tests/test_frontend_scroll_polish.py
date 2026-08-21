"""Guards for the data-grid scroll behaviour (sticky table headers).

BACKGROUND — the bug this module exists to prevent
--------------------------------------------------
Every list screen (Clients, Demands, Candidates, Users, SimpleListPage, ...)
renders its table inside

    <div class="card overflow-hidden">
      <div class="overflow-x-auto table-scroll"> ... <table class="data-table">

and src/index.css declared:

    .data-table thead th { position: sticky; top: 0; z-index: 2; }

That declaration did absolutely nothing for months. The reason is a subtle
point in CSS Overflow: when one axis is set to something other than `visible`,
the *used value* of the other axis becomes `auto` as well. So `.table-scroll`,
which only asks for `overflow-x: auto`, was already a scroll container on BOTH
axes. But it had no height constraint, so its scrollTop was permanently 0 — the
document scrolled instead. A `position: sticky` element only ever moves
relative to its NEAREST scrollport, and here that scrollport never scrolled.
Result: a header that was "sticky" in the stylesheet and static on screen.

The fix (2026-08-02) gives the box a real bounded height on >=1024px, turning it
into a genuine vertical scrollport so the sticky header engages. `max-height`
rather than `height` means short tables are untouched.

WHY THESE TESTS
---------------
The failure mode is invisible: nothing errors, nothing looks broken, the header
simply does not stick. Nobody notices in review. So the invariant is pinned
mechanically instead:

    if `thead th` is sticky, `.table-scroll` MUST also be given a bounded
    height and an explicit overflow-y in a >=1024px media query.

Breaking either half silently re-creates the original dead-CSS bug, and one of
these tests will fail.

The tests read the SOURCE stylesheet (src/index.css) for intent and the BUILT
stylesheet (app/static/spa/assets/*.css) to prove the shipped bundle actually
carries the rule — a source-only check would pass on a stale build.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC_CSS = ROOT / "app" / "frontend" / "src" / "index.css"
BUILT_DIR = ROOT / "app" / "static" / "spa" / "assets"


def _strip_comments(text: str) -> str:
    """Drop /* ... */ so prose in comments can never satisfy an assertion."""
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


@pytest.fixture(scope="module")
def source_css() -> str:
    assert SRC_CSS.is_file(), f"missing stylesheet: {SRC_CSS}"
    return _strip_comments(SRC_CSS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def built_css() -> str:
    files = sorted(BUILT_DIR.glob("index-*.css"))
    if not files:
        pytest.skip("SPA has not been built (app/static/spa/assets/index-*.css absent)")
    # Newest build wins if several hashed copies linger.
    newest = max(files, key=lambda p: p.stat().st_mtime)
    return newest.read_text(encoding="utf-8")


def _bounded_scrollport_block(css: str) -> str | None:
    """Return the `.table-scroll { ... }` body that sits inside a >=1024px query.

    Returns None when no such block exists, which is exactly the dead-CSS state
    the module guards against.
    """
    for m in re.finditer(
        r"@media\s*\(\s*min-width:\s*1024px\s*\)\s*\{(.*?)\n\}",
        css,
        flags=re.DOTALL,
    ):
        body = m.group(1)
        rule = re.search(r"\.table-scroll\s*\{([^}]*)\}", body)
        if rule and "max-height" in rule.group(1):
            return rule.group(1)
    # Minified build output has no newline before the closing brace; fall back to
    # a flat scan of the minified form.
    m = re.search(
        r"@media\s*\(min-width:\s*1024px\)\s*\{\s*\.table-scroll\s*\{([^}]*)\}",
        css,
    )
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# 1. The two halves of the invariant must both be present.
# ---------------------------------------------------------------------------


def test_thead_is_declared_sticky(source_css: str) -> None:
    """The header cells still ask to be sticky (the feature is still wanted)."""
    assert re.search(
        r"\.data-table\s+thead\s+th\s*\{[^}]*position:\s*sticky", source_css
    ), "the sticky table header declaration disappeared from src/index.css"


def test_sticky_header_has_a_bounded_scrollport(source_css: str) -> None:
    """`position: sticky` is inert unless .table-scroll can actually scroll.

    This is the whole point of the module. See the docstring for the CSS
    Overflow rule that makes the naive version a no-op.
    """
    block = _bounded_scrollport_block(source_css)
    assert block is not None, (
        "`.data-table thead th` is sticky but `.table-scroll` is never given a "
        "bounded height in a (min-width: 1024px) query. Without a scrollport "
        "that can actually scroll, the sticky header is dead CSS — it will look "
        "static on screen while appearing correct in the stylesheet."
    )
    assert "overflow-y" in block, (
        "the bounded .table-scroll must state overflow-y explicitly rather than "
        "relying on the implicit overflow-x:auto side effect"
    )


def test_bounded_scrollport_uses_max_height_not_height(source_css: str) -> None:
    """Short tables must not be letterboxed into a tall empty box."""
    block = _bounded_scrollport_block(source_css)
    assert block is not None
    assert "max-height" in block
    assert not re.search(r"(^|;)\s*height\s*:", block), (
        "use max-height so tables shorter than the viewport keep their natural "
        "height; a fixed height pads short lists with dead space"
    )


# ---------------------------------------------------------------------------
# 2. Scroll ergonomics on the bounded box.
# ---------------------------------------------------------------------------


def test_scroll_chaining_is_contained(source_css: str) -> None:
    """Reaching the end of the grid must not start scrolling the page."""
    block = _bounded_scrollport_block(source_css)
    assert block is not None
    assert "overscroll-behavior" in block and "contain" in block


def test_scrollbar_gutter_is_reserved(source_css: str) -> None:
    """Columns must not jump sideways the moment the list grows past the fold."""
    block = _bounded_scrollport_block(source_css)
    assert block is not None
    assert "scrollbar-gutter" in block and "stable" in block


# ---------------------------------------------------------------------------
# 3. A sticky header has to be opaque, and has to match the house style.
# ---------------------------------------------------------------------------


def _thead_block(css: str) -> str:
    m = re.search(
        r"\.data-table\s+thead\s+th\s*\{([^}]*background[^}]*)\}", css, flags=re.DOTALL
    )
    assert m, "could not locate the .data-table thead th rule that sets a background"
    return m.group(1)


def test_sticky_header_background_is_opaque(source_css: str) -> None:
    """Rows scrolling underneath must not show through the header."""
    block = _thead_block(source_css)
    bg = re.search(r"background(?:-color)?:\s*([^;]+);", block)
    assert bg, "the sticky header has no background at all — rows will bleed through it"
    value = bg.group(1).strip().lower()
    assert value not in {"none", "transparent"}, f"non-opaque header background: {value}"
    assert not re.search(r"rgba\([^)]*,\s*0?\.\d+\s*\)", value), (
        f"semi-transparent header background ({value}); scrolled rows will be "
        "visible through the stuck header"
    )


def test_sticky_header_is_flat_not_gradient(source_css: str) -> None:
    """House style is flat/restrained — no decorative gradients.

    A gradient on a sticky element is also the more expensive option: it forces
    the compositor to keep a painted layer for a element that repaints on every
    scroll frame.
    """
    block = _thead_block(source_css)
    assert "gradient" not in block.lower(), (
        "the table header background uses a gradient; the agreed style is a flat "
        "fill, and gradients on sticky elements repaint on every scroll frame"
    )


def test_header_underline_survives_border_collapse(source_css: str) -> None:
    """A plain border-bottom on a sticky <th> vanishes under border-collapse.

    The underline is therefore painted with an inset box-shadow instead.
    """
    block = _thead_block(source_css)
    assert "box-shadow" in block and "inset" in block, (
        "the header's bottom rule must be an inset box-shadow; a border-bottom on "
        "a sticky <th> is dropped by the border-collapse painting rules"
    )


# ---------------------------------------------------------------------------
# 4. Mobile must keep the document-scroll model.
# ---------------------------------------------------------------------------


def test_stacked_card_layout_does_not_stick(source_css: str) -> None:
    """Below 640px the table becomes stacked cards; a sticky head is nonsense there."""
    assert re.search(
        r"@media\s*\(\s*max-width:\s*640px\s*\)\s*\{[^}]*\.data-table\.responsive-cards\s+thead\s+th\s*\{[^}]*position:\s*static",
        source_css,
        flags=re.DOTALL,
    ), "the stacked-card layout must reset thead th to position: static"


def test_bounded_scrollport_is_desktop_only(source_css: str) -> None:
    """The bounded grid must not apply to phones/tablets."""
    # If .table-scroll gets a max-height anywhere OUTSIDE a min-width query,
    # small screens would get a nested scroll box too.
    without_desktop_queries = re.sub(
        r"@media\s*\([^)]*min-width[^)]*\)\s*\{.*?\n\}",
        "",
        source_css,
        flags=re.DOTALL,
    )
    offenders = re.findall(
        r"\.table-scroll\s*\{([^}]*max-height[^}]*)\}", without_desktop_queries
    )
    assert not offenders, (
        "`.table-scroll` is given a max-height outside a min-width media query, "
        f"so phones get a nested scroll box as well: {offenders}"
    )


# ---------------------------------------------------------------------------
# 5. The shipped bundle must actually carry the rule.
# ---------------------------------------------------------------------------


def test_built_bundle_ships_the_bounded_scrollport(built_css: str) -> None:
    """Guards against a stale build: source fixed, bundle not rebuilt."""
    block = _bounded_scrollport_block(built_css)
    assert block is not None, (
        "the built stylesheet has no bounded .table-scroll rule — the SPA bundle "
        "is stale relative to src/index.css. Run `npm run build` in app/frontend."
    )
    assert "overflow-y" in block
    assert "max-height" in block


def test_built_bundle_header_is_flat(built_css: str) -> None:
    """The shipped header must be the flat, opaque one."""
    m = re.search(r"\.data-table thead th\{([^}]*)\}", built_css)
    assert m, "could not find the compiled .data-table thead th rule"
    body = m.group(1)
    assert "gradient" not in body.lower(), f"shipped header still gradient: {body}"
    assert "box-shadow" in body, f"shipped header lost its inset underline: {body}"
