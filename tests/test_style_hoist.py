"""Unit tests for app/core/style_hoist.py — the zero-exception-CSP engine.

The hoister converts inline ``style="…"`` ATTRIBUTES (which CSP cannot nonce)
into rules inside a NONCED <style> element (which strict style-src-elem
allows). These tests pin the four properties the print documents depend on:

  1. every attribute is removed and replaced by a data-csp-h hook;
  2. the generated rule reproduces the declarations verbatim;
  3. <script>/<style> BODIES are never rewritten (JS template literals that
     merely *contain* ``style="…"`` text must survive byte-for-byte);
  4. hostile declaration content cannot escape the generated <style> block.
"""
from __future__ import annotations

import re

from app.core.style_hoist import hoist_inline_styles


def test_attribute_is_hoisted_into_a_nonced_style_rule():
    html = '<html><body><div class="x" style="width:42%;color:red">hi</div></body></html>'
    out = hoist_inline_styles(html, nonce="N0NCE")
    assert 'style="' not in out.split("<style", 1)[0], "attribute survived"
    m = re.search(r'data-csp-h="(h\d+)"', out)
    assert m, "no hoist hook on the element"
    key = m.group(1)
    assert '<style nonce="N0NCE">' in out
    assert f'[data-csp-h="{key}"][data-csp-h]{{width:42%;color:red;}}' in out


def test_identical_attributes_share_one_rule():
    html = ('<body><i style="width:6%"></i><i style="width:6%"></i>'
            '<i style="width:9%"></i></body>')
    out = hoist_inline_styles(html, nonce="n")
    markup, style_block = out.split("<style", 1)
    assert markup.count('data-csp-h="h0"') == 2
    assert markup.count('data-csp-h="h1"') == 1
    # exactly two distinct rules (each selector doubles the attribute, so
    # count rule OPENERS, i.e. `[data-csp-h="hN"]` with a value)
    assert len(re.findall(r'\[data-csp-h="h\d+"\]', style_block)) == 2


def test_script_bodies_are_never_rewritten():
    lit = "el.innerHTML = `<div style=\"width:${pct}%\"></div>`;"
    html = f'<body><p style="margin:0">x</p><script>{lit}</script></body>'
    out = hoist_inline_styles(html, nonce="n")
    assert lit in out, "JS template literal was corrupted by the hoister"
    assert '<p data-csp-h="h0">x</p>' in out


def test_existing_style_elements_are_never_rewritten():
    css = ".a{color:blue}"
    html = f'<head><style nonce="n">{css}</style></head><body><b style="color:red">x</b></body>'
    out = hoist_inline_styles(html, nonce="n")
    assert css in out
    assert 'style="color:red"' not in out


def test_hostile_declarations_cannot_escape_the_style_block():
    # A declaration that tries to close the rule/block or smuggle an @import
    # must be dropped, never emitted.
    html = ('<body><div style="color:red;}</style><script>alert(1)</script>{x:y;'
            '@import url(evil);width:5px">x</div></body>')
    out = hoist_inline_styles(html, nonce="n")
    assert "alert(1)" not in out
    assert "@import" not in out
    # the safe declarations survive
    assert "color:red" in out and "width:5px" in out


def test_document_without_attributes_is_returned_unchanged():
    html = "<body><p>plain</p></body>"
    assert hoist_inline_styles(html, nonce="n") == html


def test_block_lands_before_body_close():
    html = '<body><div style="top:1px">x</div></body>'
    out = hoist_inline_styles(html, nonce="n")
    assert out.rstrip().endswith("</style></body>")


def test_single_quoted_attributes_are_handled():
    html = "<body><div style='left:2mm'>x</div></body>"
    out = hoist_inline_styles(html, nonce="n")
    assert "style='" not in out.split("<style", 1)[0]
    assert "{left:2mm;}" in out


def test_html_entities_in_attribute_values_are_decoded():
    # Server code emits e.g. font-family:'X' escaped as &#x27; in attributes.
    html = '<body><div style="font-family:&#x27;Arial&#x27;,sans-serif">x</div></body>'
    out = hoist_inline_styles(html, nonce="n")
    assert "font-family:'Arial',sans-serif;" in out


def test_no_nonce_emits_bare_style_block():
    # Outside a request (unit tests) there is no CSP header either, so a bare
    # block is acceptable and must not crash.
    out = hoist_inline_styles('<body><i style="gap:1px">x</i></body>', nonce="")
    assert "<style>" in out


# ── Regression tests for bugs found while wiring this module up ──────────────

def test_data_uri_semicolons_survive():
    """The load-bearing case: document backgrounds are base64 data URIs.

    ``background-image:url('data:image/png;base64,…')`` contains a ``;`` that
    is NOT a declaration separator. Splitting on it naively cuts the URI in
    half and the letterhead/receipt/print pages render with no background.
    """
    uri = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="
    html = (f'<body><div class="doc-bg" style="background-image:url(\'{uri}\');'
            'background-size:100% 100%">x</div></body>')
    out = hoist_inline_styles(html, nonce="n")
    assert uri in out, "base64 data URI was mangled"
    assert f"background-image:url('{uri}');background-size:100% 100%;" in out


def test_attribute_value_cannot_forge_a_segment_boundary():
    """A hostile attribute value must not smuggle payload past the sanitiser.

    A regex that segments the document by searching for ``<script>…</script>``
    can find that text INSIDE an attribute value, putting a segment boundary
    mid-attribute — after which the real style attribute is never sanitised.
    The scanner must decide boundaries only outside quoted values.
    """
    html = ('<body><div title="</style><script>alert(1)</script>" '
            'style="color:red;@import url(evil)">x</div></body>')
    out = hoist_inline_styles(html, nonce="n")
    # The hostile @import must be gone, i.e. the attribute WAS sanitised.
    assert "@import" not in out
    assert "color:red" in out
    # ...and the style attribute itself is gone from the markup.
    markup = out.split("<style", 1)[0]
    assert "style=" not in markup


def test_style_inside_another_attribute_value_is_not_touched():
    html = '<body><div data-tpl=\'style="color:red"\' style="top:0">x</div></body>'
    out = hoist_inline_styles(html, nonce="n")
    assert 'data-tpl=\'style="color:red"\'' in out, "unrelated attribute rewritten"
    assert "{top:0;}" in out


def test_gt_inside_attribute_value_does_not_end_the_tag():
    html = '<body><div title="a > b" style="left:1px">x</div></body>'
    out = hoist_inline_styles(html, nonce="n")
    assert 'title="a > b"' in out
    assert "{left:1px;}" in out


def test_textarea_and_title_bodies_are_never_rewritten():
    body = 'style="color:red"'
    html = f'<head><title>{body}</title></head><body><textarea>{body}</textarea>' \
           f'<p style="margin:0">x</p></body>'
    out = hoist_inline_styles(html, nonce="n")
    assert out.count(body) == 2, "raw-text element body was rewritten"
    assert 'data-csp-h="h0"' in out


def test_unquoted_attribute_values_are_handled():
    html = '<body><div class=box style=top:1px>x</div></body>'
    out = hoist_inline_styles(html, nonce="n")
    assert "{top:1px;}" in out
    assert "class=box" in out


def test_declaration_order_and_element_rule_count_are_preserved():
    # Within-element precedence (later declaration wins) must be unchanged,
    # so declarations keep their order inside a SINGLE rule per element.
    html = '<body><div style="top:1px;top:2px">x</div></body>'
    out = hoist_inline_styles(html, nonce="n")
    assert "{top:1px;top:2px;}" in out


def test_comments_are_left_alone():
    html = '<body><!-- style="color:red" --><p style="top:0">x</p></body>'
    out = hoist_inline_styles(html, nonce="n")
    assert '<!-- style="color:red" -->' in out


def test_self_closing_and_void_tags_keep_their_slash():
    html = '<body><img src="a.png" style="width:2px"/><br></body>'
    out = hoist_inline_styles(html, nonce="n")
    assert 'src="a.png"' in out and "{width:2px;}" in out
    assert out.count("<br>") == 1


def test_doctype_is_preserved():
    html = '<!DOCTYPE html><html><body><p style="top:0">x</p></body></html>'
    out = hoist_inline_styles(html, nonce="n")
    assert out.startswith("<!DOCTYPE html>")


def test_empty_and_junk_only_attributes_are_dropped_without_a_hook():
    html = '<body><p style="">a</p><p style="  ;  ">b</p></body>'
    out = hoist_inline_styles(html, nonce="n")
    assert "data-csp-h" not in out, "hook emitted for an empty rule"
    assert "<style" not in out, "empty style block appended"
    assert "style=" not in out, "useless attribute left behind"
