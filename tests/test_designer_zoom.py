"""E3 regression -- "the page does not fit 100% in the coordinate engine".

REPORTED 2026-07-31, with a screenshot of the designer toolbar showing the
zoom indicator stuck at ``97%`` immediately after pressing ``Fit``.

ROOT CAUSE (three missing capabilities, not one wrong number)
------------------------------------------------------------
The old helper was::

    export function fitZoom(viewportWidth: number, pageW: number): number {
      const usable = viewportWidth - 48
      return usable / pageW          // clamped to [0.25, 2.0]
    }

That arithmetic is correct -- ``(643 - 48) / 595 = 0.9748`` really is 97% -- but
the *behaviour* was wrong in three ways:

1. it only ever fitted the page WIDTH, so "Fit" could leave the bottom of the
   page scrolled out of view, which is not what "fit" means when you are
   laying out a document;
2. there was no explicit 1:1 (100%) control at all, so an operator who wanted
   true actual-size could not get there;
3. a fit landing a fraction under unity reported 97%/99%, which reads as "the
   canvas is mis-scaled" and is exactly what was reported.

WHY THESE TESTS DRIVE THE REAL TypeScript
-----------------------------------------
The zoom maths lives in ``engine.ts`` and is shared by the designer canvas,
the field markers and the toolbar readout. Re-implementing it in Python would
test a copy, not the shipped code, and a copy cannot catch a regression in the
original. So these tests transform the actual ``engine.ts`` with the esbuild
binary already vendored in ``app/frontend/node_modules`` and execute it under
node. No new dependency is added, and the whole thing still runs under the one
existing ``pytest`` command.

The tests skip (never fail) when node or esbuild is unavailable, so a
Python-only checkout is not broken by them.
"""
import json
import os
import shutil
import subprocess
import textwrap

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND = os.path.join(REPO_ROOT, "app", "frontend")
ENGINE_TS = os.path.join(FRONTEND, "src", "pages", "designer", "engine.ts")
ESBUILD = os.path.join(FRONTEND, "node_modules", ".bin", "esbuild")

# The assertions below run inside node. Keeping them in one script means one
# node start-up for the whole module rather than one per assertion, which
# matters on a memory-constrained runner.
NODE_SCRIPT = textwrap.dedent(
    """
    import { fitZoom, fitPageZoom, CANVAS_PADDING, PAGE_SIZES } from './engine.mjs'

    const out = {}
    const A4_W = 595, A4_H = 842

    // 1. A viewport whose usable width lands a hair under the page width used
    //    to report 99%; it must now snap to a clean 1.0.
    out.near_unity_case = fitZoom(A4_W + CANVAS_PADDING - 2, A4_W)

    // 1b. HONESTY CHECK: a viewport genuinely NARROWER than the page cannot
    //     fit at 1:1, and must NOT be snapped up to 1 (that would silently
    //     mis-scale the canvas). 97% here is the correct answer for
    //     fit-to-width -- the remedy for the reported complaint is the new
    //     whole-page Fit and the explicit 100% control, not a wider snap.
    out.genuinely_narrow_case = fitZoom(628, A4_W)

    // 2. The snap must be tight, not sloppy: 0.995 snaps, 0.99 does not.
    //    (padding 0 keeps the arithmetic exact and independent of CANVAS_PADDING.)
    out.snap_at_995 = fitZoom(A4_W * 0.995, A4_W, 0)
    out.no_snap_at_98 = fitZoom(A4_W * 0.98, A4_W, 0)

    // 3. A genuinely small viewport must still scale DOWN, not snap to 1.
    out.narrow = fitZoom(400, A4_W)

    // 4. fitPageZoom must honour BOTH axes -- this is the capability that was
    //    missing. A viewport wide enough for the width but far too short must
    //    be limited by HEIGHT.
    out.page_limited_by_height = fitPageZoom(2000, 400, A4_W, A4_H)
    out.width_only_same_viewport = fitZoom(2000, A4_W)

    // 5. ...and be limited by WIDTH when that is the tighter axis.
    out.page_limited_by_width = fitPageZoom(400, 4000, A4_W, A4_H)

    // 6. Degenerate viewports (collapsed panel, pre-layout render) must not
    //    produce 0, Infinity or NaN -- they must fall back to 1:1.
    out.zero_w = fitZoom(0, A4_W)
    out.negative_w = fitZoom(-100, A4_W)
    out.zero_page_viewport = fitPageZoom(0, 0, A4_W, A4_H)

    // 7. Clamped into the usable band for every standard paper size.
    //    NOTE: PAGE_SIZES entries are { label, w, h } -- NOT width/height.
    //    Reading the wrong key yields undefined -> NaN, which would make this
    //    check pass vacuously for a broken helper, so the shape is asserted too.
    out.page_size_keys_ok = Object.values(PAGE_SIZES).every(
      p => Number.isFinite(p.w) && Number.isFinite(p.h) && p.w > 0 && p.h > 0
    )
    out.all_sizes_in_band = Object.keys(PAGE_SIZES).every(k => {
      const p = PAGE_SIZES[k]
      const z = fitPageZoom(1200, 900, p.w, p.h)
      return z >= 0.25 && z <= 2.0 && Number.isFinite(z)
    })

    out.padding = CANVAS_PADDING
    process.stdout.write(JSON.stringify(out))
    """
)


@pytest.fixture(scope="module")
def zoom(tmp_path_factory):
    """Transform the real engine.ts and return the node-evaluated results."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available -- skipping designer zoom unit tests")
    if not os.path.exists(ESBUILD):
        pytest.skip("esbuild is not installed in app/frontend/node_modules")
    if not os.path.exists(ENGINE_TS):
        pytest.fail(f"engine.ts not found at {ENGINE_TS}")

    work = tmp_path_factory.mktemp("designer_zoom")
    bundle = work / "engine.mjs"

    # esbuild strips the TypeScript types; engine.ts is pure arithmetic with no
    # imports, so no bundling or module resolution is required.
    proc = subprocess.run(
        [ESBUILD, ENGINE_TS, "--format=esm", "--target=es2020", f"--outfile={bundle}"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"esbuild failed:\n{proc.stderr}"

    script = work / "run.mjs"
    script.write_text(NODE_SCRIPT)
    proc = subprocess.run(
        [node, str(script)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(work),
    )
    assert proc.returncode == 0, f"node failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout)


def test_a_fit_landing_just_under_unity_reads_100_percent(zoom):
    """A sub-half-percent shortfall must report a clean 100%, not 99%."""
    assert zoom["near_unity_case"] == 1, (
        "a fit within 0.5% of 1:1 must snap to exactly 1.0, otherwise the "
        "toolbar reads 99% and implies the canvas is mis-scaled"
    )


def test_a_viewport_narrower_than_the_page_is_not_snapped_up(zoom):
    """The snap must never lie about the scale.

    This is the honest half of the E3 fix. A 628 px viewport genuinely cannot
    show a 595 pt page at 1:1 once the 48 px gutter is removed -- 97% is the
    CORRECT fit-to-width answer, and forcing it to 100% would mis-scale the
    canvas and reintroduce a real coordinate problem. What the operator was
    actually missing is a whole-page Fit (``fitPageZoom``) plus an explicit
    100% control, both of which are asserted separately below.
    """
    assert zoom["genuinely_narrow_case"] == pytest.approx((628 - 48) / 595, abs=0.002)
    assert zoom["genuinely_narrow_case"] < 1


def test_the_near_unity_snap_is_tight_not_sloppy(zoom):
    """0.995 snaps to 1; 0.98 must NOT -- a snap that wide would lie."""
    assert zoom["snap_at_995"] == 1
    assert zoom["no_snap_at_98"] < 1
    assert zoom["no_snap_at_98"] == pytest.approx(0.98, abs=0.002)


def test_a_narrow_viewport_still_scales_down(zoom):
    """The snap must not swallow genuine down-scaling."""
    assert zoom["narrow"] < 1
    assert zoom["narrow"] >= 0.25


def test_fit_page_is_limited_by_the_tighter_axis(zoom):
    """The capability that was missing: fit BOTH axes, not just width."""
    # Wide but short viewport -> height is the constraint, so fitPageZoom must
    # be well below the width-only answer for the same viewport.
    assert zoom["page_limited_by_height"] < zoom["width_only_same_viewport"], (
        "fitPageZoom ignored the viewport height -- pressing Fit would scroll "
        "the bottom of the page out of view, which is the original complaint."
    )
    # 400 px tall minus 48 padding over an 842 pt page.
    assert zoom["page_limited_by_height"] == pytest.approx((400 - 48) / 842, abs=0.002)

    # Narrow but tall viewport -> width is the constraint.
    assert zoom["page_limited_by_width"] == pytest.approx((400 - 48) / 595, abs=0.002)


def test_degenerate_viewports_fall_back_to_actual_size(zoom):
    """A collapsed or not-yet-measured viewport must never yield 0/Inf/NaN."""
    assert zoom["zero_w"] == 1
    assert zoom["negative_w"] == 1
    assert zoom["zero_page_viewport"] == 1


def test_every_standard_paper_size_stays_inside_the_zoom_band(zoom):
    # Guard the guard: if PAGE_SIZES ever changes shape, the band check below
    # would silently compare NaN and pass for free.
    assert zoom["page_size_keys_ok"] is True, (
        "PAGE_SIZES entries must expose numeric w/h -- the band check is "
        "vacuous otherwise"
    )
    assert zoom["all_sizes_in_band"] is True


DESIGNER_TSX = os.path.join(FRONTEND, "src", "pages", "DocumentCustomize.tsx")


def test_the_designer_toolbar_offers_fit_page_fit_width_and_actual_size():
    """E3's actual remedy: three distinct zoom controls, not one 'Fit'.

    Before the fix the toolbar had a single ``Fit`` button wired to
    fit-to-WIDTH, so there was no way to see the whole page and no way to
    return to a true 1:1 -- which is what "page does not fit 100%" meant.

    Asserted on the source because these are DOM-level affordances with no
    Python-reachable API. The ids are stable and exist for this purpose.
    """
    assert os.path.exists(DESIGNER_TSX), DESIGNER_TSX
    src = open(DESIGNER_TSX, encoding="utf-8").read()
    for control in (
        "designer-zoom-fit-page",   # whole page, both axes
        "designer-zoom-fit-width",  # the old behaviour, kept explicitly
        "designer-zoom-actual",     # true 1:1 / 100%
    ):
        assert control in src, (
            f"the designer toolbar is missing the '{control}' control -- E3 "
            "regression: the operator cannot reach this zoom mode"
        )
    # The whole-page helper must actually be wired in, not merely imported.
    assert "fitPageZoom" in src
    assert "fitToPage" in src


def test_auto_fit_on_load_uses_whole_page_not_width_only():
    """Opening a template must show the entire page, not a scrolled-off page."""
    src = open(DESIGNER_TSX, encoding="utf-8").read()
    assert "fitToPage()" in src, (
        "the auto-fit-on-load path must call fitToPage(); calling fitToWidth() "
        "leaves the bottom of the page below the fold on first render"
    )


def test_canvas_padding_is_exported_rather_than_hardcoded(zoom):
    """The 48 px gutter was a magic number duplicated in the component.

    Exporting it is what lets these tests pass ``padding=0`` for exact
    arithmetic, and lets the component stay in sync with the helper.
    """
    assert zoom["padding"] == 48
