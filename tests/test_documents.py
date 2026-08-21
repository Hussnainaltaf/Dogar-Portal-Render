"""Documents API: template listing + bulk-save dedupe/REPLACE-ALL semantics."""
from __future__ import annotations

from app.db.session import SessionLocal
from app.services.overlay_engine import (
    _get_cached_company_settings,
    invalidate_company_settings_cache,
    resolve_overlay_fields,
)


def test_list_templates_requires_auth(client):
    r = client.get("/api/documents/templates")
    assert r.status_code == 401


def test_list_templates_returns_seeded_templates(client, auth_headers):
    r = client.get("/api/documents/templates", headers=auth_headers)
    assert r.status_code == 200, r.text
    tpls = r.json()
    assert isinstance(tpls, list)
    # init_db seeds a known list of templates
    assert len(tpls) > 0, "no document templates seeded"
    sample = tpls[0]
    assert "id" in sample and "name" in sample


def test_create_and_bulk_save_fields_replace_all_semantics(client, auth_headers):
    # 1. create a fresh template just for this test (so we don't pollute the seeded set)
    r = client.post(
        "/api/documents/templates",
        headers=auth_headers,
        json={"name": "TestTemplateBulkSave", "data_source": "candidate"},
    )
    assert r.status_code == 200, r.text
    tpl_id = r.json()["id"]

    # 2. bulk-save N fields
    fields_v1 = [
        {"field_key": f"field_{i}", "label": f"F{i}",
         "x_mm": float(i), "y_mm": float(i), "font_size": 10, "font_bold": False}
        for i in range(5)
    ]
    r = client.post(
        f"/api/documents/templates/{tpl_id}/fields/bulk",
        headers=auth_headers,
        json={"fields": fields_v1},
    )
    assert r.status_code == 200, r.text

    # 3. verify exactly 5 saved
    r = client.get(f"/api/documents/templates/{tpl_id}/fields", headers=auth_headers)
    assert r.status_code == 200
    assert len(r.json()) == 5, f"expected 5 fields, got {len(r.json())}"

    # 4. REPLACE-ALL -- send only 3 fields, verify old 5 are gone and only 3 remain
    fields_v2 = [
        {"field_key": f"new_{i}", "label": f"N{i}",
         "x_mm": 100.0, "y_mm": 100.0, "font_size": 12, "font_bold": True}
        for i in range(3)
    ]
    r = client.post(
        f"/api/documents/templates/{tpl_id}/fields/bulk",
        headers=auth_headers,
        json={"fields": fields_v2},
    )
    assert r.status_code == 200

    r = client.get(f"/api/documents/templates/{tpl_id}/fields", headers=auth_headers)
    assert r.status_code == 200
    final = r.json()
    assert len(final) == 3, \
        f"REPLACE-ALL semantics failed: expected 3 fields, got {len(final)}"
    keys = {f["field_key"] for f in final}
    assert keys == {"new_0", "new_1", "new_2"}, f"unexpected keys: {keys}"


def test_bulk_save_dedupes_exact_position_duplicates(client, auth_headers):
    """If the same field_key at the same x/y is sent multiple times, only one survives."""
    r = client.post(
        "/api/documents/templates",
        headers=auth_headers,
        json={"name": "TestTemplateDedupe", "data_source": "candidate"},
    )
    assert r.status_code == 200
    tpl_id = r.json()["id"]

    # 10 copies of the exact same field
    dupes = [
        {"field_key": "name", "label": "Name", "x_mm": 50.0, "y_mm": 50.0,
         "font_size": 10, "font_bold": False}
        for _ in range(10)
    ]
    r = client.post(
        f"/api/documents/templates/{tpl_id}/fields/bulk",
        headers=auth_headers,
        json={"fields": dupes},
    )
    assert r.status_code == 200

    r = client.get(f"/api/documents/templates/{tpl_id}/fields", headers=auth_headers)
    assert r.status_code == 200
    saved = r.json()
    assert len(saved) == 1, \
        f"dedupe failed: 10 identical fields produced {len(saved)} rows"


def test_dedupe_endpoint_clears_existing_duplicates(client, auth_headers):
    """The /dedupe endpoint should be idempotent -- running it on clean data
    leaves the row count unchanged."""
    r = client.post(
        "/api/documents/templates",
        headers=auth_headers,
        json={"name": "TestTemplateDedupeEndpoint", "data_source": "candidate"},
    )
    assert r.status_code == 200
    tpl_id = r.json()["id"]

    r = client.post(
        f"/api/documents/templates/{tpl_id}/fields/bulk",
        headers=auth_headers,
        json={"fields": [
            {"field_key": "a", "label": "A", "x_mm": 1.0, "y_mm": 1.0,
             "font_size": 10, "font_bold": False},
            {"field_key": "b", "label": "B", "x_mm": 2.0, "y_mm": 2.0,
             "font_size": 10, "font_bold": False},
        ]},
    )
    assert r.status_code == 200

    before = client.get(f"/api/documents/templates/{tpl_id}/fields",
                        headers=auth_headers).json()
    r = client.post(f"/api/documents/templates/{tpl_id}/fields/dedupe",
                    headers=auth_headers)
    assert r.status_code == 200, r.text
    after = client.get(f"/api/documents/templates/{tpl_id}/fields",
                       headers=auth_headers).json()
    assert len(before) == len(after) == 2, \
        f"dedupe changed clean data: before={len(before)} after={len(after)}"


# ─────────────────────────── Embassy-based visa form filtering ───────────────────────────

def test_embassy_filter_karachi_hides_islamabad_form(client, auth_headers):
    """A Karachi-embassy candidate must only see the Karachi visa form."""
    r = client.get("/api/documents/templates",
                   headers=auth_headers,
                   params={"category": "Visa Process", "embassy": "Karachi Embassy"})
    assert r.status_code == 200, r.text
    names = [t["name"].lower() for t in r.json()]
    assert not any("islamabad" in n for n in names), \
        f"Islamabad form leaked into Karachi embassy list: {names}"
    assert any("karachi" in n for n in names), \
        f"Karachi visa form missing from Karachi embassy list: {names}"


def test_embassy_filter_islamabad_hides_karachi_form(client, auth_headers):
    r = client.get("/api/documents/templates",
                   headers=auth_headers,
                   params={"category": "Visa Process", "embassy": "Islamabad Embassy"})
    assert r.status_code == 200, r.text
    names = [t["name"].lower() for t in r.json()]
    assert not any("karachi" in n for n in names), \
        f"Karachi form leaked into Islamabad embassy list: {names}"
    assert any("islamabad" in n for n in names), \
        f"Islamabad visa form missing from Islamabad embassy list: {names}"


def test_no_embassy_shows_all_visa_forms(client, auth_headers):
    """Without ?embassy, both embassy forms are available (no accidental hiding)."""
    r = client.get("/api/documents/templates",
                   headers=auth_headers, params={"category": "Visa Process"})
    assert r.status_code == 200
    names = [t["name"].lower() for t in r.json()]
    assert any("karachi" in n for n in names)
    assert any("islamabad" in n for n in names)


def test_no_duplicate_karachi_visa_template(client, auth_headers):
    """Exactly ONE Karachi visa application template must exist (duplicate removed)."""
    r = client.get("/api/documents/templates", headers=auth_headers,
                   params={"category": "Visa Process"})
    assert r.status_code == 200
    khi = [t["name"] for t in r.json()
           if "karachi" in t["name"].lower() or "khi" in t["name"].lower()]
    assert len(khi) == 1, f"expected exactly 1 Karachi visa template, got: {khi}"


# ── Printing bug regression tests ─────────────────────────────────────────────
# The reported bug: portal documents report a "successful" print job but never
# physically print, while receipts (which use `@page { size: A4 portrait }`)
# print fine. Root cause: the portal print view emitted a CUSTOM mm @page size
# (209.90mm 296.93mm) that real printer drivers treat as an unloaded custom
# paper, silently discarding output. The fix emits a NAMED standard size.

def _first_template_id(client, auth_headers, category=None):
    params = {"category": category} if category else None
    r = client.get("/api/documents/templates", headers=auth_headers, params=params)
    assert r.status_code == 200
    tpls = r.json()
    assert tpls, "no templates seeded"
    return tpls[0]["id"]


def _first_a4_template_id(client, auth_headers):
    """Return a template that is genuinely A4 (595x842pt).

    FIX 2026-08-03: this used to be ``_first_template_id()``, i.e. "whatever
    template happens to sort first". That passed only because the seeder wrongly
    forced every template to A4 -- the fresh-install geometry defect. Now that
    templates 1/7/8 correctly come up Letter (612x792), picking the first
    template returns the Allied Bank form and the A4 assertion legitimately
    fails. The test's *fixture* was wrong, not the print view: selecting by the
    actual page size is what the test always meant.
    """
    r = client.get("/api/documents/templates", headers=auth_headers)
    assert r.status_code == 200
    tpls = r.json()
    assert tpls, "no templates seeded"
    a4 = [
        t for t in tpls
        if round(t.get("page_width") or 0) == 595
        and round(t.get("page_height") or 0) == 842
    ]
    assert a4, (
        "no A4 template is seeded, so this test cannot assert anything about "
        f"the named A4 print size. Seeded sizes: "
        f"{sorted({(round(t.get('page_width') or 0), round(t.get('page_height') or 0)) for t in tpls})}"
    )
    return a4[0]["id"]


def test_print_view_uses_named_a4_page_size(client, auth_headers):
    """Standard A4 (595x842pt) templates must emit `size: A4 portrait`, NOT a
    custom mm size — this is what makes physical printing work reliably."""
    tpl_id = _first_a4_template_id(client, auth_headers)
    r = client.get(f"/api/documents/templates/{tpl_id}/print",
                   headers=auth_headers, params={"auto": 0})
    assert r.status_code == 200
    html = r.text
    assert "@page" in html
    # Named size present …
    assert "size: A4 portrait" in html, "print view must use the named A4 size"
    # … and the fragile custom mm size is GONE from the @page rule.
    assert "size: 209.90mm 296.93mm" not in html
    assert "size: 209.9mm" not in html


def test_print_view_uses_matching_letter_and_legal_page_sizes(client, auth_headers):
    """Saved paper geometry must reach browser print using named driver sizes."""
    for name, page_size, width, height, css_name in (
        ("PrintLetterRegression", "letter", 612, 792, "Letter"),
        ("PrintLegalRegression", "legal", 612, 1008, "Legal"),
    ):
        created = client.post(
            "/api/documents/templates",
            headers=auth_headers,
            json={
                "name": name,
                "data_source": "candidate",
                "page_size": page_size,
                "orientation": "portrait",
                "page_width": width,
                "page_height": height,
            },
        )
        assert created.status_code == 200, created.text
        tpl_id = created.json()["id"]
        printed = client.get(
            f"/api/documents/templates/{tpl_id}/print",
            headers=auth_headers,
            params={"auto": 0},
        )
        assert printed.status_code == 200, printed.text
        assert f"size: {css_name} portrait" in printed.text


def test_print_view_requires_auth(client):
    r = client.get("/api/documents/templates/1/print")
    assert r.status_code == 401


# ── CSP-blocked style regression ──────────────────────────────────────────────
# The reported field bug ("fields collapsed to top-left, no coordinate overlay,
# 4-5 min wait for a broken print dialog"): the print HTML shipped a bare
# ``<style>`` block with NO nonce. Strict CSP style-src-elem 'self' 'nonce-...'
# silently drops the block, so .doc-page{position:relative} and
# .ov-field{position:absolute} never take effect and every field falls into
# plain document flow at the top-left corner (matches the reproduction
# screenshot exactly). This test asserts the nonce is present so any future
# refactor that drops it fails CI immediately.
def test_print_view_style_and_script_are_nonced(client, auth_headers):
    """The print endpoint's <style> AND <script> MUST carry a CSP nonce so
    strict style-src-elem / script-src don't silently drop them (which
    collapses the entire coordinate overlay to plain flow at top-left)."""
    tpl_id = _first_template_id(client, auth_headers)
    r = client.get(
        f"/api/documents/templates/{tpl_id}/print",
        headers=auth_headers,
        params={"auto": 0},
    )
    assert r.status_code == 200
    html = r.text
    # Every <style> and <script> in the response must carry nonce="..."
    # (empty results ARE allowed here — the point is that no BARE tag exists).
    import re as _re
    bare_style = _re.findall(r"<style(?![^>]*\bnonce=)[^>]*>", html)
    bare_script = _re.findall(r"<script(?![^>]*\bnonce=)(?![^>]*\bsrc=)[^>]*>", html)
    assert not bare_style, (
        f"print HTML must not contain a non-nonced <style> tag "
        f"(strict CSP would drop it and collapse the overlay); "
        f"found: {bare_style[:3]}"
    )
    assert not bare_script, (
        f"print HTML must not contain a non-nonced inline <script> tag "
        f"(strict CSP would drop it and break auto-print / letterhead toggle); "
        f"found: {bare_script[:3]}"
    )
    # And at least one nonced style block DOES exist — otherwise the layout
    # would have no CSS at all.
    assert _re.search(r'<style\s+nonce="[^"]+"', html), \
        "print HTML must contain at least one nonced <style> block"


def test_candidate_print_profile_style_is_nonced(client, auth_headers):
    """Same guard for /api/candidates/{id}/print-profile (letterhead-based
    candidate CV). Was also silently broken by the CSP hardening on
    2026-07-20 until this fix pass."""
    # Create a minimal candidate so we have an id to hit.
    r = client.post(
        "/api/candidates/",
        headers=auth_headers,
        json={"full_name": "CSP Nonce Guard Test", "cnic": "1234567890123"},
    )
    assert r.status_code in (200, 201), r.text
    cand_id = r.json()["id"]
    r = client.get(
        f"/api/candidates/{cand_id}/print-profile",
        headers=auth_headers,
        params={"auto_print": False},
    )
    assert r.status_code == 200
    html = r.text
    import re as _re
    bare_style = _re.findall(r"<style(?![^>]*\bnonce=)[^>]*>", html)
    assert not bare_style, (
        f"candidate print-profile must not contain a non-nonced <style>; "
        f"found: {bare_style[:3]}"
    )
    assert _re.search(r'<style\s+nonce="[^"]+"', html)


def test_company_settings_cache_uses_detached_scalar_snapshot(client, auth_headers):
    """Cached document settings remain readable after the loading Session closes."""
    response = client.get("/api/settings/general", headers=auth_headers)
    assert response.status_code == 200, response.text

    invalidate_company_settings_cache()
    db = SessionLocal()
    try:
        company = _get_cached_company_settings(db)
    finally:
        db.close()

    assert company is not None
    assert not hasattr(company, "_sa_instance_state")
    fields = resolve_overlay_fields(company=company)
    assert fields["customer_name"] == response.json()["company_name"]
