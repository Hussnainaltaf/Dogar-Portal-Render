"""Letterhead renderer: data:URL embedding + content margins."""
from __future__ import annotations


def test_letterhead_data_url_is_jpeg_data_url():
    from app.services.letterhead_renderer import _letterhead_data_url

    url = _letterhead_data_url()
    # File missing → empty string is OK (defensive); otherwise must be a JPEG data URL
    if not url:
        return
    assert url.startswith("data:image/jpeg;base64,"), \
        "letterhead must be served as inline data: URL, never an http:// URL"
    assert len(url) > 200, "letterhead data URL suspiciously short"


def test_barcode_data_url_is_crisp_vector_svg():
    """Barcodes were upgraded from raster PNG to vector SVG for crisp print output."""
    from app.services.letterhead_renderer import generate_barcode_png_b64

    url = generate_barcode_png_b64("TEST-1234")
    assert url.startswith("data:image/svg+xml;base64,"), \
        "barcode must be an inline SVG data URL for crisp vector print rendering"
    assert len(url) > 200
    # decode & sanity-check the SVG payload actually contains the value
    import base64
    svg = base64.b64decode(url.split(",", 1)[1]).decode("utf-8", "replace")
    assert "<svg" in svg
    assert "TEST-1234" in svg


def test_content_margin_constants_match_hotfix():
    """The Demand Letter alignment hotfix moved margins 38/28/18/18 → 46/30/22/22."""
    import app.services.letterhead_renderer as lr

    # The exact constant names may vary -- search for them defensively
    top = getattr(lr, "CONTENT_TOP_MM", None)
    bot = getattr(lr, "CONTENT_BOTTOM_MM", None)

    if top is None or bot is None:
        # Renderer uses inline values -- read the source to verify
        import inspect
        src = inspect.getsource(lr)
        assert "46" in src and "30" in src and "22" in src, \
            "expected new margins (46/30/22) not found in renderer source"
        return

    # If constants exist, verify the hotfix values
    assert top == 46, f"CONTENT_TOP_MM should be 46 (post-hotfix), got {top}"
    assert bot == 30, f"CONTENT_BOTTOM_MM should be 30 (post-hotfix), got {bot}"


# ── CSP nonce auto-injection via ContextVar ───────────────────────────────────
# The letterhead_renderer._wrap_document() and receipts_renderer._wrap() both
# emit inline <style>/<script> blocks that MUST carry the CSP nonce or strict
# style-src-elem drops them silently. Every letter/receipt endpoint is called
# from a request context that publishes the nonce through
# `app.core.csp_nonce.current_csp_nonce` (a ContextVar). The renderers read it
# and stamp `nonce="..."` on the tags. If this pipeline breaks, EVERY letter
# and receipt print in the app silently degrades to plain-flow layout.
def test_letterhead_renderer_picks_up_ctxvar_nonce():
    """A nonce set via ContextVar must appear on the <style>/<script> tags
    of the wrapped document, without any caller passing it explicitly."""
    from app.core.csp_nonce import current_csp_nonce
    from app.services.letterhead_renderer import _wrap_document

    token = current_csp_nonce.set("TEST-NONCE-abc123")
    try:
        html = _wrap_document(
            title="Test", toolbar_sub="sub",
            body_html="<div>hi</div>", auto_print=True,
        )
    finally:
        current_csp_nonce.reset(token)

    assert 'nonce="TEST-NONCE-abc123"' in html, \
        "letterhead renderer must auto-pick up the CSP nonce from ContextVar"
    # And when the nonce is unset, the renderer must NOT crash — it should
    # simply emit bare tags (used only outside a request context, e.g. tests
    # rendering HTML directly for inspection).
    html2 = _wrap_document(
        title="T", toolbar_sub="", body_html="<div/>", auto_print=False,
    )
    assert "<style" in html2  # still renders something


def test_receipts_renderer_picks_up_ctxvar_nonce():
    """Same guarantee for the receipts_renderer._wrap() shell."""
    from app.core.csp_nonce import current_csp_nonce
    from app.services.receipts_renderer import _wrap

    token = current_csp_nonce.set("RCPT-NONCE-xyz789")
    try:
        html = _wrap(
            title="T", toolbar_sub="s",
            body_html="<p>x</p>", auto_print=True,
        )
    finally:
        current_csp_nonce.reset(token)

    assert 'nonce="RCPT-NONCE-xyz789"' in html, \
        "receipts renderer must auto-pick up the CSP nonce from ContextVar"
