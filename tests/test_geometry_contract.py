"""The cross-language coordinate contract, actually enforced.

WHY THIS MODULE EXISTS (2026-08-18)
===================================
Three engines decide where a value is printed on a government form:

  1. the React Template Designer canvas   -- designer/engine.ts   (TypeScript)
  2. the HTML print view                  -- endpoints/documents.py (Python)
  3. the ReportLab PDF renderer           -- services/pdf_engine.py (Python)

Before this module, they agreed only by CODE COMMENT. engine.ts said
"MUST match ASCENT in the print view (documents.py)"; documents.py said
"matches reportlab". Both statements were assertions about the code that the
code never checked. A grep over tests/ found ZERO tests referencing ASCENT,
ASCENT_RATIO or PAGE_SIZES, so either copy could have been edited and every
document would have quietly printed its values off the boxes -- the worst
failure mode available, because the PDF still renders and merely looks
slightly wrong.

Two real mismatches existed and are fixed by app/core/geometry_contract.py:

  * ASCENT duplicated as 0.8 (TS) and 0.800 (Python), unguarded.
  * PAGE_SIZES genuinely disagreed with the renderer. engine.ts stored rounded
    integers while pdf_engine.py fell back to reportlab's exact floats:
    A4 height 842 vs 841.8898, A3 1191 vs 1190.5512, A5 595 vs 595.2756.
    Since PDF y is measured from the BOTTOM, a page-height disagreement
    displaces every field on the page. Both "A4"s were reachable in the same
    request path, because the DocumentTemplate columns default to the rounded
    595x842 while the NULL fallback used the float.

These tests read the ACTUAL TypeScript source and compare it against the
Python authority, so divergence fails the suite instead of being described in
a comment. That is the whole point: a contract nobody executes is not a
contract.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.geometry_contract import (
    ASCENT_RATIO,
    BARCODE_HEIGHT_FALLBACK,
    DEFAULT_PAGE_HEIGHT,
    DEFAULT_PAGE_WIDTH,
    HEIGHT_FALLBACK_FONT_MULTIPLE,
    HEIGHT_FALLBACK_MINIMUM,
    PAGE_SIZES,
    PT_TO_CSS,
    baseline_pdf_y,
    css_top_from_pdf_y,
    effective_height,
    resolve_page_size,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE_TS = REPO_ROOT / "app" / "frontend" / "src" / "pages" / "designer" / "engine.ts"


# ---------------------------------------------------------------------------
# Reading the TypeScript side
# ---------------------------------------------------------------------------
def _engine_source() -> str:
    if not ENGINE_TS.is_file():
        pytest.skip(f"designer engine source not present: {ENGINE_TS}")
    return ENGINE_TS.read_text(encoding="utf-8", errors="replace")


def _executable_lines(source: str) -> str:
    """The source with whole-line ``#`` comments and docstring prose removed.

    A reversion gate must inspect what RUNS, not what is written about what used
    to run. The fixes in this codebase deliberately quote the old buggy
    expression in a comment so the next reader can see the defect; a gate that
    cannot distinguish code from prose would make documenting a bug a test
    failure, and the cheapest way to go green would be to delete the
    explanation. This keeps the check strict about code while leaving the
    history readable.

    Deliberately simple and line-oriented: it strips full-line comments and
    trailing comments. It does not attempt to parse strings, because the
    patterns this gate looks for are assignments, and an assignment hidden
    inside a string literal is not executable either.
    """
    out = []
    for raw in source.splitlines():
        stripped = raw.strip()
        if stripped.startswith("#"):
            continue
        if "#" in raw:
            raw = raw.split("#", 1)[0]
        out.append(raw)
    return "\n".join(out)


def _ts_number(source: str, name: str) -> float:
    """Extract ``export const <name> = <number>`` from the TypeScript source.

    Deliberately strict: if the declaration cannot be found the test FAILS
    rather than skipping, because "the constant vanished" is exactly the
    refactor this gate exists to catch. A gate that skips when its anchor moves
    protects nothing -- the same anchor-decay that has bitten mutations in this
    codebase twice.
    """
    m = re.search(
        rf"export\s+const\s+{re.escape(name)}\s*(?::\s*number\s*)?=\s*(-?[\d.]+)",
        source,
    )
    assert m, (
        f"could not find `export const {name} = <number>` in {ENGINE_TS.name}. "
        "If the constant was renamed or removed, the Python authority in "
        "app/core/geometry_contract.py and this gate must be updated in the "
        "same commit -- otherwise the designer and the renderers can drift "
        "apart silently and documents print off their boxes."
    )
    return float(m.group(1))


def _ts_page_sizes(source: str) -> dict[str, tuple[int, int]]:
    """Parse the PAGE_SIZES preset table out of engine.ts."""
    block = re.search(
        r"export\s+const\s+PAGE_SIZES[^=]*=\s*\{(.*?)\n\}", source, re.S,
    )
    assert block, (
        f"could not find the PAGE_SIZES table in {ENGINE_TS.name}; the "
        "designer's page presets are part of the coordinate contract."
    )
    out: dict[str, tuple[int, int]] = {}
    for key, w, h in re.findall(
        r"(\w+)\s*:\s*\{[^}]*?\bw\s*:\s*(-?[\d.]+)\s*,\s*h\s*:\s*(-?[\d.]+)",
        block.group(1),
    ):
        out[key] = (float(w), float(h))
    assert out, "PAGE_SIZES was found but no w/h entries could be parsed from it"
    return out


# ---------------------------------------------------------------------------
# The gate itself
# ---------------------------------------------------------------------------
def test_the_typescript_ascent_ratio_matches_the_python_authority():
    """A divergent ascent ratio moves every text baseline off its box.

    The ascent converts a ReportLab BASELINE into a CSS box TOP. If the
    designer and the renderers disagree, what the operator positions is not
    what prints, by (delta * font_size) points on every single field.
    """
    ts = _ts_number(_engine_source(), "ASCENT_RATIO")
    assert ts == pytest.approx(ASCENT_RATIO, abs=1e-9), (
        f"engine.ts ASCENT_RATIO={ts} but app/core/geometry_contract.py "
        f"ASCENT_RATIO={ASCENT_RATIO}. The designer canvas and the PDF/print "
        "renderers would place text baselines differently, so every document "
        "would print its values off the boxes the operator designed."
    )


def test_the_typescript_pt_to_css_matches_the_python_authority():
    """1 pt == 1 px at zoom 1.0 underpins every conversion in both directions."""
    ts = _ts_number(_engine_source(), "PT_TO_CSS")
    assert ts == pytest.approx(PT_TO_CSS, abs=1e-9), (
        f"engine.ts PT_TO_CSS={ts} but the Python authority says {PT_TO_CSS}. "
        "Every pt<->px conversion in the designer would be scaled wrongly."
    )


def test_the_typescript_page_presets_match_the_python_authority():
    """MEASURED mismatch: the presets really did disagree with the renderer.

    A4 842 vs reportlab 841.8898, A3 1191 vs 1190.5512, A5 595 vs 595.2756.
    PDF y is measured from the bottom edge, so a page-height disagreement
    displaces every field on the page relative to the scanned artwork.
    """
    ts = _ts_page_sizes(_engine_source())

    missing = set(PAGE_SIZES) - set(ts)
    assert not missing, (
        f"page presets present in the Python authority but absent from "
        f"{ENGINE_TS.name}: {sorted(missing)}. A template saved with one of "
        "these sizes could not be re-opened at the same geometry."
    )
    extra = set(ts) - set(PAGE_SIZES)
    assert not extra, (
        f"page presets offered by the designer but unknown to the renderers: "
        f"{sorted(extra)}. The operator could author against a page the PDF "
        "engine cannot reproduce."
    )
    for key, (want_w, want_h) in PAGE_SIZES.items():
        got_w, got_h = ts[key]
        assert (got_w, got_h) == pytest.approx((want_w, want_h), abs=1e-9), (
            f"page preset {key!r} disagrees: engine.ts has "
            f"{got_w}x{got_h}, the renderers use {want_w}x{want_h}. Because "
            "PDF y is measured from the bottom edge, a page-height difference "
            "displaces EVERY field on the page."
        )


def test_the_typescript_height_fallback_matches_the_python_authority():
    """A field with no explicit height must get the same box in all engines.

    If the fallbacks differ, the baseline drifts by (h - font_size) points --
    the exact defect the ``or 20`` fallback used to cause against the PDF.
    """
    source = _engine_source()
    m = re.search(
        r"effectiveHeight\s*=\s*\([^)]*\)\s*:\s*number\s*=>\s*"
        r"hPt\s*>\s*0\s*\?\s*hPt\s*:\s*Math\.max\(\s*fontSize\s*\*\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\)",
        source,
    )
    assert m, (
        f"could not parse effectiveHeight() out of {ENGINE_TS.name}. It is "
        "part of the coordinate contract: all three engines must use the same "
        "fallback box height for a field whose height is NULL or 0."
    )
    ts_multiple, ts_minimum = float(m.group(1)), float(m.group(2))
    assert ts_multiple == pytest.approx(HEIGHT_FALLBACK_FONT_MULTIPLE, abs=1e-9), (
        f"engine.ts height fallback multiple {ts_multiple} != "
        f"{HEIGHT_FALLBACK_FONT_MULTIPLE} in the Python authority"
    )
    assert ts_minimum == pytest.approx(HEIGHT_FALLBACK_MINIMUM, abs=1e-9), (
        f"engine.ts height fallback minimum {ts_minimum} != "
        f"{HEIGHT_FALLBACK_MINIMUM} in the Python authority"
    )


# ---------------------------------------------------------------------------
# The Python side of the contract, independent of the TypeScript file
# ---------------------------------------------------------------------------
def test_the_pdf_engine_no_longer_carries_its_own_page_fallback():
    """MEASURED: pdf_engine.py used reportlab's exact A4 as its NULL fallback.

    ``float(template.page_width or A4[0])`` produced 595.2756 x 841.8898 while
    every authored coordinate had been placed against the rounded 595 x 842
    that the DocumentTemplate columns default to. Same template, two different
    pages, depending only on whether the columns had been written.
    """
    src = (REPO_ROOT / "app" / "services" / "pdf_engine.py").read_text(errors="replace")
    assert "resolve_page_size(" in src, (
        "pdf_engine.py must resolve its page box through the shared "
        "geometry contract, not with a private fallback."
    )
    # Scan EXECUTABLE lines only. The fix deliberately quotes the old buggy
    # expression in a comment so the next reader knows what was wrong and why;
    # a gate that cannot tell code from prose would forbid documenting the bug,
    # and the honest way to satisfy it would be to delete the explanation.
    code = _executable_lines(src)
    assert not re.search(
        r"page_width\s*=\s*float\(\s*template\.page_width\s*or\s*A4\[0\]", code,
    ), (
        "pdf_engine.py has reverted to reportlab's exact A4 as the NULL page "
        "fallback. That page is 0.1102 pt shorter than the 595x842 the "
        "designer authors against, which displaces every field on the page."
    )


def test_the_comment_stripper_still_sees_real_code():
    """The reversion gates must not be blinded by their own comment stripping.

    ``_executable_lines`` was added so the fix could quote the old buggy
    expression in a comment without failing its own gate. That is only
    acceptable if the stripper still exposes genuine code. If it stripped too
    much, both reversion gates above would pass unconditionally and protect
    nothing -- a green check that cannot fail, which is worse than a red one.
    """
    sample = (
        "# page_width = float(template.page_width or A4[0])  <- old, commented\n"
        "page_width = float(template.page_width or A4[0])  # <- real reversion\n"
        "other = 1\n"
    )
    code = _executable_lines(sample)

    # The live assignment survives ...
    assert re.search(
        r"page_width\s*=\s*float\(\s*template\.page_width\s*or\s*A4\[0\]", code,
    ), "the stripper removed executable code; the reversion gates would be blind"
    # ... and exactly once, i.e. the commented copy really was removed.
    assert len(re.findall(r"A4\[0\]", code)) == 1, (
        "the commented-out copy was not stripped, so documenting the old bug "
        "would fail the gate"
    )
    assert "<- old, commented" not in code
    assert "<- real reversion" not in code, "trailing comments should be stripped"
    assert "other = 1" in code


def test_the_print_view_no_longer_carries_its_own_ascent_literal():
    """The print view must derive the ascent, not restate it.

    A second literal is how the two copies drifted in the first place.
    """
    src = (REPO_ROOT / "app" / "api" / "endpoints" / "documents.py").read_text(errors="replace")
    assert "ASCENT_RATIO" in src, (
        "documents.py must import ASCENT_RATIO from the geometry contract."
    )
    assert not re.search(r"ASCENT\s*=\s*0\.8\d*\s*(?:#|$)", src, re.M), (
        "documents.py has re-introduced a hard-coded ascent literal. Derive it "
        "from app/core/geometry_contract.py so the designer, print view and "
        "PDF renderer cannot drift apart."
    )


def test_the_default_page_matches_the_template_column_defaults():
    """The contract default must equal the DB column defaults.

    If these disagree, a template renders on a different page depending purely
    on whether page_width/page_height happen to have been written -- which is
    precisely the mismatch that shipped.
    """
    src = (REPO_ROOT / "app" / "models" / "document.py").read_text(errors="replace")
    w = re.search(r"page_width\s*=\s*Column\(\s*Float\s*,\s*default\s*=\s*(-?[\d.]+)", src)
    h = re.search(r"page_height\s*=\s*Column\(\s*Float\s*,\s*default\s*=\s*(-?[\d.]+)", src)
    assert w and h, "could not read the page_width/page_height column defaults"
    assert float(w.group(1)) == float(DEFAULT_PAGE_WIDTH), (
        f"DocumentTemplate.page_width default {w.group(1)} != contract "
        f"{DEFAULT_PAGE_WIDTH}"
    )
    assert float(h.group(1)) == float(DEFAULT_PAGE_HEIGHT), (
        f"DocumentTemplate.page_height default {h.group(1)} != contract "
        f"{DEFAULT_PAGE_HEIGHT}"
    )


@pytest.mark.parametrize(
    "width,height,expected",
    [
        (None, None, (595.0, 842.0)),       # both NULL -> canonical A4
        (0, 0, (595.0, 842.0)),             # zero is as unset as NULL
        (612, 792, (612.0, 792.0)),         # explicit Letter is respected
        (612, None, (612.0, 842.0)),        # partial: only the missing axis falls back
        (None, 1008, (595.0, 1008.0)),
    ],
)
def test_resolve_page_size_falls_back_per_axis(width, height, expected):
    """Each axis falls back independently, and 0 counts as unset.

    A half-written template (width set, height NULL) must not silently produce
    a mixed page nobody designed against.
    """
    assert resolve_page_size(width, height) == pytest.approx(expected, abs=1e-9)


@pytest.mark.parametrize(
    "height,font_size,expected",
    [
        (20.0, 11.0, 20.0),      # explicit height wins
        (None, 11.0, 14.0),      # 11*1.25 = 13.75 -> floor of 14 applies
        (0, 11.0, 14.0),         # 0 is unset
        (None, 40.0, 50.0),      # 40*1.25 = 50 -> the multiple applies
        (None, 4.0, 14.0),       # tiny font still gets the 14pt floor
    ],
)
def test_effective_height_matches_the_documented_fallback(height, font_size, expected):
    """Pins the shared fallback, including which branch wins at the boundary."""
    assert effective_height(height, font_size) == pytest.approx(expected, abs=1e-9)


def test_the_barcode_fallback_is_not_the_text_fallback():
    """Barcodes use a fixed 32pt box, not the font-derived one.

    Conflating the two drifted the barcode baseline up to 6pt against print.
    """
    assert BARCODE_HEIGHT_FALLBACK == pytest.approx(32.0)
    assert BARCODE_HEIGHT_FALLBACK != effective_height(None, 11.0)


def test_baseline_math_round_trips_through_css_and_back():
    """The baseline conversion must be self-consistent, not merely present.

    Positioning a box, converting to CSS and back must land on the original
    coordinate; otherwise repeated designer saves would walk a field down the
    page one rounding at a time.
    """
    page_h = float(DEFAULT_PAGE_HEIGHT)
    for y, h in [(100.0, 14.0), (0.0, 14.0), (735.11, 12.0), (500.5, 40.0)]:
        top = css_top_from_pdf_y(y, h, page_h)
        back = page_h - (top / PT_TO_CSS) - h
        assert back == pytest.approx(y, abs=1e-9), (
            f"round-trip drift for y={y} h={h}: got {back}"
        )


def test_the_baseline_sits_at_the_box_top_not_the_box_bottom():
    """The whole point of the baseline formula.

    ReportLab draws text with its baseline at the given y, but the designer
    flows text from the TOP of the marker box. If the baseline were left at the
    box bottom, every field with h != font_size would print (h - font_size)pt
    too low -- 26pt on a 40pt box, which is a whole line on a bank slip.
    """
    y, h, fs = 100.0, 40.0, 14.0
    assert baseline_pdf_y(y, h, fs) == pytest.approx(126.0), (
        "baseline should be y + (h - font_size) = 100 + 26"
    )
    # Degenerate box: never push the baseline BELOW the box origin.
    assert baseline_pdf_y(y, 5.0, 14.0) == pytest.approx(100.0), (
        "a box shorter than the font must clamp to the box origin, not "
        "produce a negative offset that lifts text out of its box"
    )


def test_every_page_preset_is_portrait_and_positive():
    """A preset with a zero/negative or landscape-swapped axis is a trap.

    The designer swaps w/h for landscape itself, so the table must be portrait;
    a pre-swapped entry would double-swap and render the page sideways.
    """
    for key, (w, h) in PAGE_SIZES.items():
        assert w > 0 and h > 0, f"page preset {key!r} has a non-positive axis: {w}x{h}"
        assert h >= w, (
            f"page preset {key!r} is {w}x{h}, i.e. landscape. The presets must "
            "be portrait because the designer applies the landscape swap "
            "itself; a pre-swapped entry double-swaps."
        )
