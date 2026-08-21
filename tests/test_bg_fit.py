"""Tests for background-image fit modes in the PDF coordinate engine.

The designer lets an operator choose how a background image is placed:
  fill    – stretch to the whole page (default; correct for full-page scans)
  contain – scale to fit inside the page, aspect ratio preserved (barcodes,
            logos, stamps stay undistorted and scannable)
  actual  – intrinsic px = pt, no scaling (most faithful for small assets)

_bg_placement returns (x, y, draw_w, draw_h) in PDF points with a bottom-left
origin (ReportLab convention). These tests lock in the geometry so a future
change can never silently re-introduce barcode distortion.
"""
from app.services.pdf_engine import _bg_placement

PAGE_W, PAGE_H = 612.0, 792.0  # US Letter


def test_fill_stretches_to_full_page():
    x, y, w, h = _bg_placement("fill", 300, 100, PAGE_W, PAGE_H)
    assert (x, y) == (0.0, 0.0)
    assert (w, h) == (PAGE_W, PAGE_H)


def test_contain_preserves_aspect_ratio():
    iw, ih = 300, 100
    x, y, w, h = _bg_placement("contain", iw, ih, PAGE_W, PAGE_H)
    # aspect ratio of the drawn image must equal the source aspect ratio
    assert round(w / h, 4) == round(iw / ih, 4)
    # must fit inside the page
    assert w <= PAGE_W + 1e-6 and h <= PAGE_H + 1e-6
    # top-left anchored (image sits at the top of the page)
    assert x == 0.0
    assert round(y + h, 4) == PAGE_H


def test_contain_uses_the_limiting_dimension():
    # A very wide image is limited by width -> drawn width == page width.
    x, y, w, h = _bg_placement("contain", 1200, 100, PAGE_W, PAGE_H)
    assert round(w, 4) == PAGE_W
    # A very tall image is limited by height -> drawn height == page height.
    x, y, w, h = _bg_placement("contain", 100, 1600, PAGE_W, PAGE_H)
    assert round(h, 4) == PAGE_H


def test_actual_keeps_intrinsic_pixel_size():
    x, y, w, h = _bg_placement("actual", 300, 100, PAGE_W, PAGE_H)
    assert (w, h) == (300.0, 100.0)
    assert x == 0.0
    assert y == PAGE_H - 100.0  # top-left anchored


def test_zero_size_image_falls_back_to_fill():
    # Guards against a division-by-zero on a broken image.
    x, y, w, h = _bg_placement("contain", 0, 0, PAGE_W, PAGE_H)
    assert (x, y, w, h) == (0.0, 0.0, PAGE_W, PAGE_H)


def test_unknown_fit_falls_back_to_fill():
    x, y, w, h = _bg_placement("nonsense", 300, 100, PAGE_W, PAGE_H)
    assert (w, h) == (PAGE_W, PAGE_H)


# ===========================================================================
# E2 REGRESSION -- "the field is not where I placed it" (reported 2026-07-31)
# ===========================================================================
# The operator's complaint was that a field positioned in the coordinate
# engine did not land in the same place in the print preview. The FIELD math
# was never wrong: designer (engine.ts), print preview (documents.py) and the
# PDF engine (pdf_engine.py) all agree on
#     baseline_pdf_y = y + max(height - font_size, 0)
#     text_top_pt    = page_h - (baseline_pdf_y + 0.800 * font_size)
#
# What was wrong is the BACKGROUND. The designer and the PDF engine both
# honour the template's `bg_fit` mode (fill / contain / actual), but the print
# preview hardcoded `background-size: 100% 100%` -- i.e. it ALWAYS stretched.
# So on any `contain` or `actual` template the form artwork rendered at a
# different scale and offset than the designer had shown, while the field sat
# at exactly the right coordinate. To the operator the field looks moved,
# because the thing it is supposed to line up with moved underneath it.
#
# These tests lock the preview onto `bg_fit`. They deliberately assert on the
# emitted CSS rather than on a pixel diff, because the CSS *is* the contract
# between the three renderers.
_FIT_TO_EXPECTED_CSS = {
    # fill -> stretch both axes to the page box (the historical behaviour,
    # still correct for edge-to-edge full-page scans).
    "fill": "background-size:100% 100%",
    # contain -> scale down keeping aspect, pinned to the top-left, exactly
    # like _bg_placement("contain", ...) above.
    "contain": "background-size:contain",
}


def _make_bg_fit_template(client, auth_headers, fit):
    """Create a template whose designer meta-fence carries `bg_fit`.

    Two details are load-bearing and were discovered by these tests failing:

    1. The background must be a background that actually EXISTS on disk
       (`dogar_letterhead.jpg`), because the preview inlines it as a base64
       data URI and emits no `.doc-bg` layer at all when the file is missing.
    2. `background_image` is NOT part of `DocumentTemplateCreate` -- only the
       PUT schema and the dedicated upload endpoint accept it -- so POSTing it
       is silently ignored and it has to be set with a follow-up PUT.
    3. The template must own at least one field, because the preview builds
       its page blocks by iterating the fields -- a field-less template
       renders zero pages and therefore zero background layers.
    """
    import json as _json

    fence = "<!--designer:" + _json.dumps({"bg_fit": fit}) + "-->"
    r = client.post(
        "/api/documents/templates",
        headers=auth_headers,
        json={
            "name": f"E2BgFitPreview_{fit}",
            "data_source": "candidate",
            "description": f"E2 regression fixture{fence}",
            "page_width": 595,
            "page_height": 842,
        },
    )
    assert r.status_code == 200, r.text
    tpl_id = r.json()["id"]

    r = client.put(
        f"/api/documents/templates/{tpl_id}",
        headers=auth_headers,
        json={"background_image": "dogar_letterhead.jpg"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["background_image"], "background_image did not persist"

    r = client.post(
        f"/api/documents/templates/{tpl_id}/fields/bulk",
        headers=auth_headers,
        json={
            "fields": [
                {
                    "field_key": "full_name",
                    "label": "Full Name",
                    "x_mm": 20.0,
                    "y_mm": 20.0,
                    "font_size": 11,
                    "font_bold": False,
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    return tpl_id


def _doc_bg_declarations(html: str) -> list[str]:
    """Return the per-element background declarations of every .doc-bg layer.

    Handles BOTH representations so this file keeps testing behaviour rather
    than syntax:

      * ``<div class="doc-bg" style="…">``            (pre-hoist)
      * ``<div class="doc-bg" data-csp-h="hN">`` plus
        ``[data-csp-h="hN"][data-csp-h]{…}`` in a <style> block  (post-hoist,
        see app/core/style_hoist.py)

    Raises if a .doc-bg element carries a hoist hook whose rule is missing —
    that would mean the declarations were dropped, which must fail loudly
    rather than silently return "no declarations" and pass a weaker check.
    """
    import re as _re

    out = [m for m in _re.findall(r'<div class="doc-bg" style="([^"]*)"', html)]

    for key in _re.findall(r'<div class="doc-bg"[^>]*?data-csp-h="(h\d+)"', html):
        rule = _re.search(
            r'\[data-csp-h="' + _re.escape(key) + r'"\]\[data-csp-h\]\{([^}]*)\}',
            html,
        )
        assert rule, (
            f".doc-bg carries hoist hook {key!r} but no matching rule was "
            f"emitted — the background declarations were lost"
        )
        out.append(rule.group(1))
    return out


def test_print_preview_honours_each_bg_fit_mode(client, auth_headers):
    """The preview must emit bg_fit-specific background CSS, not always 100%."""
    for fit, expected in _FIT_TO_EXPECTED_CSS.items():
        tpl_id = _make_bg_fit_template(client, auth_headers, fit)
        r = client.get(
            f"/api/documents/templates/{tpl_id}/print", headers=auth_headers
        )
        assert r.status_code == 200, r.text
        html = r.text
        assert expected in html, (
            f"bg_fit={fit!r} preview did not emit {expected!r}. This is the E2 "
            "regression: the preview ignored bg_fit and always stretched the "
            "background, so the artwork moved out from under correctly-placed "
            "fields."
        )
        if fit != "fill":
            # A non-fill mode must anchor top-left, matching _bg_placement's
            # `y = page_h - drawn_h` (bottom-origin) top-left anchor.
            assert "background-position:top left" in html


def test_contain_preview_does_not_also_stretch(client, auth_headers):
    """`contain` must NOT leave the old stretch rule on the same element.

    Mutation guard: if someone re-adds the hardcoded 100% 100% inline, both
    declarations would appear on the .doc-bg element and the LAST one wins in
    CSS -- silently resurrecting the bug while the test above still passed.
    """
    tpl_id = _make_bg_fit_template(client, auth_headers, "contain")
    r = client.get(f"/api/documents/templates/{tpl_id}/print", headers=auth_headers)
    assert r.status_code == 200, r.text
    # Isolate the per-element background declarations only -- the stylesheet
    # keeps a 100% 100% *fallback* rule by design, which is harmless because the
    # per-element declarations always override it.
    #
    # NOTE (2026-08-04): these declarations used to live in a style="…"
    # ATTRIBUTE on the .doc-bg div. They are now hoisted into a nonced <style>
    # rule keyed by data-csp-h so the response can be served under a CSP with
    # no 'unsafe-inline' (see app/core/style_hoist.py). The BUG THIS GUARDS IS
    # UNCHANGED -- only where the declarations are written down. Scraping the
    # rule instead of the attribute keeps the mutation guard intact; asserting
    # on the whole document would not, because of that deliberate fallback.
    bg_decls = _doc_bg_declarations(r.text)
    assert bg_decls, (
        "print preview emitted no .doc-bg element with per-element background "
        "declarations (neither a style attribute nor a hoisted data-csp-h rule)"
    )
    for style in bg_decls:
        assert "contain" in style
        assert "100% 100%" not in style, (
            "background declarations contain BOTH contain and the 100% 100% "
            "stretch; the later declaration wins and the E2 bug returns"
        )


def test_unknown_bg_fit_in_the_fence_degrades_to_fill(client, auth_headers):
    """A corrupt/unknown bg_fit must fall back to fill, never raise."""
    tpl_id = _make_bg_fit_template(client, auth_headers, "nonsense-mode")
    r = client.get(f"/api/documents/templates/{tpl_id}/print", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert "background-size:100% 100%" in r.text
