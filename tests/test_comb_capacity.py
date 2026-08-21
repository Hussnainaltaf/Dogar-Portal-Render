"""Comb capacity policy: what a fixed-width character grid prints when the
value is longer than the grid.

WHY THIS FILE EXISTS
====================
The OEP form's Address field is a *comb*: two rows of 13 pre-printed boxes, one
character per box, 26 characters in total. 2,198 of the 2,636 non-empty
candidate addresses in this database (83.4%) are longer than that. Median 35,
p90 52, max 91.

Before this policy the three renderers each sliced the value themselves and let
the render loop drop whatever ran past ``cell_count``. The result, on real data:

    full : 'HARI PURKI P.O KAMONKI TEHSIL KAMON KI DISTT GUJRANWALA'
    print: 'HARI PURKI P.O KAMONKI TEH'

Cut mid-word (so it reads as a typo rather than an omission) and the DISTRICT --
the part that actually identifies the location -- gone, with nothing anywhere to
say so. On a government form that is the worst available failure mode: the
document looks complete.

WHAT IS TESTED HERE
===================
1. The decision ladder, at every boundary the policy has to handle:
   empty / short / exactly-capacity / over-capacity / unfittable / misconfigured.
2. That abbreviations are ATTESTED, not invented. An abbreviation is legitimate
   only if the short form is already present in the operators' own data. TEH,
   DISTT, MOH, PO, ST and VILL are. RD is not (233 uses of ROAD, zero of RD), so
   ROAD is not abbreviated -- inventing a convention and calling it policy is
   the thing this test prevents.
3. That capacity is ``max(offset + cell_count)`` and not ``sum(cell_count)``.
4. That the canonical value is never modified.
5. That a row can never bleed into the field printed beside it.
6. That the three renderers AGREE -- see
   ``test_all_three_render_paths_agree_on_the_same_value``, which is the whole
   reason the policy is one shared module instead of three implementations.

Each test asserts on BEHAVIOUR (call the function, inspect the result), not on
the presence of source text. A grep for "abbreviat" would stay green if the
ladder were deleted.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.services.comb_capacity import (
    ATTESTED_ABBREVIATIONS,
    REJECTED_ABBREVIATIONS,
    STATUS_ABBREVIATED,
    STATUS_CAPACITY_EXCEEDED,
    STATUS_EMPTY,
    STATUS_FITS,
    STATUS_TRUNCATED_AT_WORD,
    apply_attested_abbreviations,
    comb_capacity_for_key,
    comb_rows_from_fields,
    fit_comb_text,
    resolve_comb_row,
    row_text_for_offset,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The real OEP address comb: two 13-cell rows, the second continuing at 13.
OEP_ADDRESS_CAPACITY = 26

#: A real record from the shipped database, and the exact failure it caused.
REAL_LONG_ADDRESS = "HARI PURKI P.O KAMONKI TEHSIL KAMON KI DISTT GUJRANWALA"


def _oep_address_rows() -> list[dict]:
    """The two OEP address comb rows, shaped like an overlay payload."""
    return [
        {"field_type": "char_cells", "field_key": "address", "page": 1,
         "meta": {"cell_count": 13, "cell_width": 31.47, "format": "text",
                  "char_offset": 0}},
        {"field_type": "char_cells", "field_key": "address", "page": 1,
         "meta": {"cell_count": 13, "cell_width": 31.45, "format": "text",
                  "char_offset": 13}},
    ]


# ===========================================================================
# The four boundary cases the policy must handle explicitly
# ===========================================================================
@pytest.mark.parametrize("value", ["", "   ", "\t\n", None])
def test_empty_values_print_nothing_and_are_not_flagged(value):
    """An address nobody entered is not a capacity problem."""
    fit = fit_comb_text(value, OEP_ADDRESS_CAPACITY)
    assert fit.status == STATUS_EMPTY
    assert fit.printed == ""
    assert fit.review_required is False, (
        "an empty value must not raise a review flag -- if it did, the flag "
        "would fire on most records and operators would learn to ignore it"
    )


def test_a_short_address_is_printed_verbatim():
    fit = fit_comb_text("MOH SADIQ ABAD", OEP_ADDRESS_CAPACITY)
    assert fit.status == STATUS_FITS
    assert fit.printed == "MOH SADIQ ABAD"
    assert fit.dropped == ""
    assert fit.abbreviations == (), (
        "a value that already fits must not be abbreviated -- abbreviating it "
        "would change the printed form of a correct record for no reason"
    )
    assert fit.review_required is False


def test_a_value_of_exactly_capacity_fits_untouched():
    """The off-by-one that would silently eat the last character."""
    value = "A" * OEP_ADDRESS_CAPACITY
    fit = fit_comb_text(value, OEP_ADDRESS_CAPACITY)
    assert fit.status == STATUS_FITS
    assert fit.printed == value
    assert len(fit.printed) == OEP_ADDRESS_CAPACITY
    assert fit.dropped == ""
    assert fit.review_required is False


def test_one_character_over_capacity_is_flagged_not_silently_cut():
    """26 fits, 27 must NOT quietly become 26."""
    fit = fit_comb_text(("A" * 20) + " " + ("B" * 6), OEP_ADDRESS_CAPACITY)
    assert fit.status == STATUS_TRUNCATED_AT_WORD
    assert fit.review_required is True
    assert fit.dropped, "the dropped text must be reported, not discarded"


def test_the_documented_real_world_failure_is_no_longer_silent():
    """The exact record from the audit: what changed, and what did not."""
    fit = fit_comb_text(REAL_LONG_ADDRESS, OEP_ADDRESS_CAPACITY)

    # Still cannot print 55 characters in 26 boxes -- the box pitch is fixed by
    # the pre-printed government form. What changed is that it is now REPORTED.
    assert fit.review_required is True
    assert fit.status == STATUS_TRUNCATED_AT_WORD

    # The cut is on a word boundary, so the result never reads as a typo.
    assert not fit.printed.endswith("TEHS")
    assert fit.printed == fit.printed.rstrip()

    # Everything dropped is named, so an operator can see what is missing.
    assert "GUJRANWALA" in fit.dropped
    assert fit.to_dict()["dropped_chars"] == len(fit.dropped) > 0

    # And the source data is untouched.
    assert fit.canonical == REAL_LONG_ADDRESS


def test_a_single_word_longer_than_the_comb_prints_nothing():
    """No word boundary to cut on -> print nothing rather than a fragment.

    A fragment of a single long word is indistinguishable from a complete short
    word, so it is worse than a visibly empty field: it cannot be spotted.
    """
    fit = fit_comb_text("SUPERCALIFRAGILISTICEXPIALIDOCIOUS", OEP_ADDRESS_CAPACITY)
    assert fit.status == STATUS_CAPACITY_EXCEEDED
    assert fit.printed == ""
    assert fit.review_required is True
    assert fit.canonical == "SUPERCALIFRAGILISTICEXPIALIDOCIOUS"


def test_a_comb_with_no_cells_is_a_configuration_error_not_an_empty_value():
    fit = fit_comb_text("SOME ADDRESS", 0)
    assert fit.status == STATUS_CAPACITY_EXCEEDED
    assert fit.printed == ""
    assert fit.review_required is True
    assert "Template Designer" in fit.reason, (
        "the message must tell the operator WHERE to fix it"
    )


# ===========================================================================
# Never destroy source data
# ===========================================================================
@pytest.mark.parametrize("value", [
    "",
    "SHORT ADDR",
    "A" * 26,
    REAL_LONG_ADDRESS,
    "SUPERCALIFRAGILISTICEXPIALIDOCIOUSLONGWORD",
    "VILLAGE CHAK 42 TEHSIL DASKA DISTRICT SIALKOT",
])
def test_the_canonical_value_is_never_modified(value):
    """Whatever the policy decides to PRINT, the record's own value survives.

    Only whitespace is collapsed (a display concern); no characters are lost.
    """
    fit = fit_comb_text(value, OEP_ADDRESS_CAPACITY)
    expected = re.sub(r"\s+", " ", value).strip()
    assert fit.canonical == expected
    # Nothing in the pipeline may abbreviate the canonical form.
    for long_form in ATTESTED_ABBREVIATIONS:
        if long_form in expected:
            assert long_form in fit.canonical, (
                f"{long_form!r} was abbreviated in the CANONICAL value -- "
                "abbreviation is a print-representation concern only"
            )


# ===========================================================================
# Abbreviations must be attested, never invented
# ===========================================================================
def test_attested_abbreviations_are_applied_when_they_make_the_value_fit():
    fit = fit_comb_text("VILLAGE CHAK 42 TEHSIL DASKA", OEP_ADDRESS_CAPACITY)
    assert fit.status == STATUS_ABBREVIATED
    assert fit.printed == "VILL CHAK 42 TEH DASKA"
    assert len(fit.printed) <= OEP_ADDRESS_CAPACITY
    assert ("VILLAGE", "VILL") in fit.abbreviations
    assert ("TEHSIL", "TEH") in fit.abbreviations
    assert fit.review_required is False, (
        "an attested abbreviation loses no information -- it must not demand "
        "review, or the flag becomes noise"
    )


#: Forms that MUST NOT be abbreviated, hardcoded here on purpose.
#:
#: This list is deliberately NOT derived from ``REJECTED_ABBREVIATIONS``.
#: Parametrising over that table made this test circular: mutation BC renamed
#: the table's "ROAD" key to "XROADX" and the test happily passed, because it
#: then only checked that XROADX (a word nobody uses) was not abbreviated. The
#: table is documentation; the *ratchet* has to be independent of it, or editing
#: the table silently removes the protection.
#:
#: Each entry was verified against the operators' own address data: the long
#: form is in use and the short form is NOT, so emitting the short form would
#: print an address in a style no operator has ever written.
MUST_NOT_ABBREVIATE = {
    "ROAD": "RD",      # 233 uses of ROAD, ZERO uses of RD
    "NUMBER": "NO",
    "HOUSE": "H",
    "NEAR": "NR",
    "COLONY": "COL",
    "TOWN": "TWN",
}


@pytest.mark.parametrize("long_form,forbidden_short", sorted(MUST_NOT_ABBREVIATE.items()))
def test_rejected_abbreviations_are_never_applied(long_form, forbidden_short):
    """The guard against inventing a convention.

    ``ROAD -> RD`` is the load-bearing case: it is the obvious abbreviation, it
    would have helped the fit rate, and it is WRONG here -- the corpus has 233
    uses of ROAD and zero of RD. Shipping it would mean this system prints a
    form of the address that no operator has ever written.
    """
    assert long_form not in ATTESTED_ABBREVIATIONS, (
        f"{long_form!r} was added to ATTESTED_ABBREVIATIONS. That is an "
        f"INVENTED abbreviation: the corpus does not use {forbidden_short!r}. "
        "Re-run the frequency check in the module docstring before adding it."
    )
    text = f"{long_form} SOMEPLACE"
    out, applied = apply_attested_abbreviations(text)
    assert long_form in out, f"{long_form!r} must survive untouched"
    assert not any(s == forbidden_short for _, s in applied)


def test_the_rejected_table_documents_exactly_what_the_ratchet_enforces():
    """Keep the in-source documentation honest about the enforced list.

    ``REJECTED_ABBREVIATIONS`` exists so the reasoning is discoverable at the
    point someone is tempted to add an entry. If the two drift apart, the
    comment lies -- so this asserts they agree, WITHOUT letting the table drive
    the enforcement (see MUST_NOT_ABBREVIATE).
    """
    assert set(REJECTED_ABBREVIATIONS) == set(MUST_NOT_ABBREVIATE), (
        "REJECTED_ABBREVIATIONS and the enforced MUST_NOT_ABBREVIATE list have "
        "diverged. Update both, and record the frequency evidence for any new "
        f"entry.\n  in source only: {set(REJECTED_ABBREVIATIONS) - set(MUST_NOT_ABBREVIATE)}"
        f"\n  in test only:   {set(MUST_NOT_ABBREVIATE) - set(REJECTED_ABBREVIATIONS)}"
    )


def test_no_attested_abbreviation_is_also_a_rejected_one():
    """The policy must not contradict itself."""
    overlap = set(ATTESTED_ABBREVIATIONS) & set(MUST_NOT_ABBREVIATE)
    assert not overlap, f"{overlap} appear as both attested and rejected"


def test_road_is_specifically_not_abbreviated_end_to_end():
    """The same rejection, asserted through the public decision function."""
    fit = fit_comb_text("MAIN ROAD NEAR BIG HOUSE COLONY", OEP_ADDRESS_CAPACITY)
    assert "RD" not in fit.printed.split(), "invented abbreviation reached print"
    assert "NR" not in fit.printed.split()
    assert not any(short in ("RD", "NR", "COL", "TWN")
                   for _, short in fit.abbreviations)


def test_abbreviation_only_matches_whole_tokens():
    """'STREET' abbreviates; 'STREETLIGHT' must not become 'STLIGHT'."""
    out, applied = apply_attested_abbreviations("STREETLIGHT PLAZA")
    assert out == "STREETLIGHT PLAZA"
    assert applied == ()


def test_abbreviation_is_deterministic():
    """Same input, same output -- every time, no dict-ordering dependence."""
    value = "VILLAGE MOHALLAH POST STREET TEHSIL DISTRICT SOMEWHERE"
    results = {fit_comb_text(value, OEP_ADDRESS_CAPACITY).printed for _ in range(50)}
    assert len(results) == 1, f"non-deterministic output: {results}"


def test_abbreviation_alone_is_not_treated_as_a_solution():
    """Abbreviation is marginal and the policy must not pretend otherwise.

    Measured on the real corpus, attested abbreviation lifts the fit rate from
    16.6% to 19.6% -- only +79 records; 80.4% are still over capacity. So a
    long address that abbreviation cannot rescue must still be flagged, not
    quietly shortened and passed off as fine.
    """
    fit = fit_comb_text(
        "MOHALLAH ISLAMPURA STREET 7 TEHSIL SHAKARGARH DISTRICT NAROWAL",
        OEP_ADDRESS_CAPACITY,
    )
    assert fit.abbreviations, "abbreviations should have been attempted"
    assert fit.status == STATUS_TRUNCATED_AT_WORD
    assert fit.review_required is True


# ===========================================================================
# Truncation is at a word boundary, never mid-word
# ===========================================================================
@pytest.mark.parametrize("value", [
    REAL_LONG_ADDRESS,
    "MOHALLAH ISLAMPURA STREET 7 TEHSIL SHAKARGARH DISTRICT NAROWAL",
    "P.O KHAS MALO CHAT TEH O DISTT SIALKOT",
    "HOUSE 12 BLOCK C GULBERG III LAHORE CANTT PUNJAB PAKISTAN",
])
def test_truncation_never_splits_a_word(value):
    """A mid-word cut reads as a typo; a word-boundary cut reads as an omission.

    Verified structurally: the printed form must be a whole-token prefix of the
    string the policy actually worked from.
    """
    fit = fit_comb_text(value, OEP_ADDRESS_CAPACITY)
    if fit.status != STATUS_TRUNCATED_AT_WORD:
        pytest.skip(f"{value!r} did not need truncation ({fit.status})")
    # Rebuild the string truncation worked from (post-abbreviation).
    working = fit.canonical
    for long_form, short in fit.abbreviations:
        working = re.sub(rf"\b{re.escape(long_form)}\b", short, working)
    working = re.sub(r"\s+", " ", working).strip()
    printed_tokens = fit.printed.split()
    working_tokens = working.split()
    assert printed_tokens == working_tokens[:len(printed_tokens)], (
        f"{fit.printed!r} is not a whole-token prefix of {working!r} -- a word "
        "was split"
    )


def test_the_dropped_text_is_reported_so_the_loss_is_auditable():
    fit = fit_comb_text(REAL_LONG_ADDRESS, OEP_ADDRESS_CAPACITY)
    assert fit.dropped, "dropped text must be reported"
    assert fit.dropped in fit.reason, (
        "the reason shown to the operator must name what will be missing, not "
        "merely count it"
    )
    assert fit.printed not in ("", fit.canonical)


def test_the_reason_is_human_readable_and_actionable():
    for value in (REAL_LONG_ADDRESS, "X" * 40, "OK ADDRESS"):
        fit = fit_comb_text(value, OEP_ADDRESS_CAPACITY)
        assert fit.reason and len(fit.reason) > 15
        assert not fit.reason.startswith(("Error", "Exception", "Traceback"))


# ===========================================================================
# Capacity discovery: max(offset+count), NOT sum(count)
# ===========================================================================
def test_multi_row_capacity_is_max_offset_plus_count():
    """Two 13-cell rows at offsets 0 and 13 hold 26 characters."""
    assert comb_capacity_for_key([(0, 13), (13, 13)]) == 26


def test_a_duplicated_row_without_char_offset_reports_its_real_capacity():
    """The template bug that ``sum(count)`` would hide.

    Two rows both at offset 0 do not hold 26 characters -- the second row simply
    reprints the first 13. Summing would report 26 and let a 26-character
    address through as "fits" while half of it never appeared on the form.
    """
    assert comb_capacity_for_key([(0, 13), (0, 13)]) == 13, (
        "capacity must be max(offset+count); sum(count) would report 26 and "
        "hide the missing char_offset"
    )


def test_capacity_ignores_rows_with_no_cells():
    assert comb_capacity_for_key([(0, 13), (13, 0)]) == 13
    assert comb_capacity_for_key([]) == 0


def test_a_three_row_comb_is_supported():
    assert comb_capacity_for_key([(0, 14), (14, 14), (28, 14)]) == 42


def test_rows_are_discovered_from_the_template_field_list():
    rows = comb_rows_from_fields(_oep_address_rows(), "address", page=1)
    assert sorted(rows) == [(0, 13), (13, 13)]
    assert comb_capacity_for_key(rows) == OEP_ADDRESS_CAPACITY


def test_row_discovery_does_not_mix_up_different_keys_or_pages():
    fields = _oep_address_rows() + [
        {"field_type": "char_cells", "field_key": "full_name", "page": 1,
         "meta": {"cell_count": 14, "char_offset": 0}},
        {"field_type": "char_cells", "field_key": "address", "page": 2,
         "meta": {"cell_count": 20, "char_offset": 0}},
        {"field_type": "text", "field_key": "address", "page": 1,
         "meta": {"cell_count": 99}},
    ]
    rows = comb_rows_from_fields(fields, "address", page=1)
    assert sorted(rows) == [(0, 13), (13, 13)], (
        "capacity leaked in from another key, another page, or a non-comb field"
    )


def test_row_discovery_accepts_orm_style_objects():
    """The PDF engine passes ORM rows; the API passes dicts. One extractor."""
    class _F:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    fields = [
        _F(field_type="char_cells", field_key="address", page=1,
           meta={"cell_count": 13, "char_offset": 0}),
        _F(field_type="char_cells", field_key="address", page=1,
           meta={"cell_count": 13, "char_offset": 13}),
    ]
    assert comb_capacity_for_key(comb_rows_from_fields(fields, "address", 1)) == 26


# ===========================================================================
# No overflow into neighbouring comb fields
# ===========================================================================
def test_a_row_never_prints_more_characters_than_it_has_cells():
    """The containment guarantee.

    Row 1 of the OEP address is followed on the page by other fields. If it
    returned more than 13 characters the extra glyphs would be drawn past its
    last box, on top of whatever is printed beside it.
    """
    printed = "A" * 100
    assert len(row_text_for_offset(printed, 0, 13)) == 13
    assert len(row_text_for_offset(printed, 13, 13)) == 13


def test_rows_partition_the_decided_string_without_gaps_or_overlap():
    fit = fit_comb_text(REAL_LONG_ADDRESS, OEP_ADDRESS_CAPACITY)
    row1 = row_text_for_offset(fit.printed, 0, 13)
    row2 = row_text_for_offset(fit.printed, 13, 13)
    assert row1 + row2 == fit.printed, (
        "the rows must exactly reconstruct the decided string -- a gap loses a "
        "character, an overlap prints one twice"
    )


def test_a_continuation_rows_leading_space_is_preserved():
    """The bug a naive ``.strip()`` in the draw helper would cause.

    When the 14th character of the decided string is the space between two
    words, row 2 legitimately BEGINS with a space -- that blank box is what
    separates the words on the printed form. Stripping it shifts every
    remaining character one cell to the left, so the whole row is wrong.
    """
    fit = fit_comb_text(REAL_LONG_ADDRESS, OEP_ADDRESS_CAPACITY)
    row2 = row_text_for_offset(fit.printed, 13, 13)
    assert row2.startswith(" "), (
        f"expected a leading blank cell, got {row2!r} -- fixture assumption "
        "changed; pick a value whose 14th character is a space"
    )
    assert row2 != row2.lstrip()


def test_a_row_beyond_the_end_of_the_value_prints_nothing():
    fit = fit_comb_text("SHORT", OEP_ADDRESS_CAPACITY)
    assert row_text_for_offset(fit.printed, 13, 13) == ""


def test_row_slicing_with_no_cells_returns_nothing():
    assert row_text_for_offset("ANYTHING", 0, 0) == ""
    assert row_text_for_offset("ANYTHING", 0, -5) == ""


# ===========================================================================
# resolve_comb_row: the shared entry point
# ===========================================================================
def test_resolve_uses_the_full_multi_row_capacity_not_one_rows_share():
    """The core fix.

    Row 1 has 13 cells. Judged against 13, a 20-character address is "too
    long". Judged against the comb's real 26, it fits. Every renderer used to
    make the first mistake.
    """
    rows = _oep_address_rows()
    value = "MOH SADIQ ABAD DASKA"  # 20 chars: over 13, under 26
    row1, fit = resolve_comb_row(value, rows[0]["meta"], rows, "address", 1)
    assert fit.capacity == OEP_ADDRESS_CAPACITY
    assert fit.status == STATUS_FITS
    assert fit.review_required is False
    row2, fit2 = resolve_comb_row(value, rows[1]["meta"], rows, "address", 1)
    assert row1 + row2 == value


def test_every_row_of_a_comb_reports_the_identical_verdict():
    """Both rows must agree, or the designer would badge one and not the other."""
    rows = _oep_address_rows()
    _, fit1 = resolve_comb_row(REAL_LONG_ADDRESS, rows[0]["meta"], rows, "address", 1)
    _, fit2 = resolve_comb_row(REAL_LONG_ADDRESS, rows[1]["meta"], rows, "address", 1)
    assert fit1.to_dict() == fit2.to_dict()


def test_resolve_falls_back_to_this_rows_capacity_for_a_single_row_comb():
    """A template with no sibling rows keeps its pre-existing behaviour."""
    meta = {"cell_count": 13, "format": "digits"}
    row, fit = resolve_comb_row("1234567890123", meta, [], "cnic", 1)
    assert fit.capacity == 13
    assert row == "1234567890123"


def test_resolve_applies_the_format_vocabulary_before_measuring():
    """A dashed CNIC is 15 characters typed but 13 in cells.

    Measuring capacity before separator-stripping would flag every correctly
    entered CNIC as over-capacity.
    """
    meta = {"cell_count": 13, "format": "digits", "char_offset": 0}
    row, fit = resolve_comb_row("34503-0359241-1", meta, [], "cnic", 1)
    assert row == "3450303592411"
    assert fit.status == STATUS_FITS
    assert fit.review_required is False


def test_resolve_keeps_spaces_for_text_format_and_drops_them_otherwise():
    text_row, _ = resolve_comb_row(
        "AB CD", {"cell_count": 13, "format": "text"}, [], "address", 1)
    assert text_row == "AB CD", "format 'text' must keep the blank cell"
    alnum_row, _ = resolve_comb_row(
        "AB CD", {"cell_count": 13, "format": "alnum"}, [], "x", 1)
    assert alnum_row == "ABCD", "a non-text comb must not spend a cell on a space"


# ===========================================================================
# The three render paths must agree
# ===========================================================================
def test_all_three_render_paths_agree_on_the_same_value():
    """The reason this policy is one module and not three implementations.

    This project's three renderers -- the ReportLab PDF engine, the HTML print
    path and the designer preview -- have ALREADY disagreed twice: the HTML path
    had no ``char_cells`` support at all until 2026-08-11, and the designer drew
    combs as one continuous string until 2026-08-12. Each was internally
    consistent, which is exactly why nobody noticed.

    Here all three are asked what they would print, by the route each one
    actually uses in production, and the answers must be byte-identical.
    """
    rows = _oep_address_rows()
    value = REAL_LONG_ADDRESS

    # Path A -- the PDF engine and the HTML print path both call
    # resolve_comb_row() directly, per row.
    pdf_rows = [resolve_comb_row(value, r["meta"], rows, "address", 1)[0]
                for r in rows]

    # Path B -- the designer preview receives the decision as data from
    # _build_overlay_payload, which calls the same function over the payload's
    # own field list. Reproduce that call shape (dicts, not ORM rows).
    payload_fields = [dict(r, id=i + 1, value=value) for i, r in enumerate(rows)]
    api_rows = [
        resolve_comb_row(f["value"], f["meta"], payload_fields, "address", 1)[0]
        for f in payload_fields
    ]

    assert pdf_rows == api_rows, (
        "the render paths disagree about what to print -- the Print button, the "
        "PDF download and the on-screen preview would produce different "
        f"documents from one template: {pdf_rows} vs {api_rows}"
    )
    # And they reconstruct exactly the decided string, nothing more.
    fit = fit_comb_text(value, OEP_ADDRESS_CAPACITY)
    assert "".join(pdf_rows) == fit.printed


def test_the_pdf_engine_actually_routes_through_the_policy():
    """Behavioural, not a grep: drive the real renderer and read the result.

    A source-text assertion ("does pdf_engine mention resolve_comb_row?") would
    stay green if the call were left in place but its result ignored. So this
    calls ``_render_field`` on a real ReportLab canvas with a stub that records
    what reached the drawing helper.
    """
    import app.services.pdf_engine as pe

    class _Meta(dict):
        pass

    class _Tpl:
        pass

    class _Field:
        field_type = "char_cells"
        field_key = "address"
        page = 1
        x, y, width, height = 155.6, 420.0, 409.1, 14.0
        font_size = 11
        font_bold = True
        font_italic = False
        color = "#000000"
        align = "left"
        static_value = ""

    tpl = _Tpl()
    row1, row2 = _Field(), _Field()
    row1.meta = {"cell_count": 13, "cell_width": 31.47, "format": "text",
                 "char_offset": 0}
    row2.meta = {"cell_count": 13, "cell_width": 31.45, "format": "text",
                 "char_offset": 13}
    tpl.fields = [row1, row2]
    row1.template = tpl
    row2.template = tpl

    # Capture what actually reaches the PAGE, cell by cell.
    #
    # An earlier version of this test spied on the text passed INTO
    # _draw_char_cells. Mutation BN proved that insufficient: re-enabling the
    # legacy `.strip()` INSIDE the helper corrupted the continuation row (eating
    # its leading blank cell and shifting every later character one box left)
    # while the text entering the function was still perfect. The only honest
    # assertion is on the glyphs and the x-coordinates ReportLab is asked to
    # draw.
    import io as _io

    from reportlab.pdfgen import canvas as _canvas

    drawn: list[tuple[int, float, str]] = []
    c = _canvas.Canvas(_io.BytesIO())
    real_draw = c.drawCentredString
    row_no = [0]

    def _spy_draw(x, y, text, *a, **kw):
        drawn.append((row_no[0], round(float(x), 2), text))
        return real_draw(x, y, text, *a, **kw)

    c.drawCentredString = _spy_draw
    row_no[0] = 1
    pe._render_field(c, row1, REAL_LONG_ADDRESS, None, None, 595.0, 842.0)
    row_no[0] = 2
    pe._render_field(c, row2, REAL_LONG_ADDRESS, None, None, 595.0, 842.0)

    expected = fit_comb_text(REAL_LONG_ADDRESS, OEP_ADDRESS_CAPACITY).printed

    # Reconstruct each row from the glyphs actually drawn, ordering by the
    # x-coordinate so the check does not depend on emission order.
    def _row(n: int) -> list[tuple[float, str]]:
        return sorted((x, ch) for r, x, ch in drawn if r == n)

    row1_glyphs, row2_glyphs = _row(1), _row(2)

    # A blank cell draws nothing, so rebuild against each row's cell pitch to
    # detect a SHIFT (the exact damage BN caused) rather than only a wrong glyph.
    def _reconstruct(glyphs, x0: float, pitch: float, count: int) -> str:
        cells = [" "] * count
        for x, ch in glyphs:
            # x is the cell centre: index = (centre - x0 - pitch/2) / pitch
            idx = int(round((x - x0 - pitch / 2) / pitch))
            assert 0 <= idx < count, (
                f"a character was drawn at x={x} -> cell {idx}, outside this "
                f"row's {count} cells: it would overprint the neighbouring field"
            )
            cells[idx] = ch
        return "".join(cells)

    got1 = _reconstruct(row1_glyphs, row1.x, row1.meta["cell_width"], 13)
    got2 = _reconstruct(row2_glyphs, row2.x, row2.meta["cell_width"], 13)

    # `_reconstruct` returns one character per CELL, so a row whose value ends
    # before its last box is padded with blanks. Pad the expectation the same way
    # rather than stripping the result -- stripping the right-hand side would be
    # harmless, but stripping to compare is the habit that lets a LEADING blank
    # (the word separator, and the exact casualty of mutation BN) go unchecked.
    want1 = expected[:13].ljust(13)
    want2 = expected[13:26].ljust(13)

    assert got1 == want1, (
        f"row 1 rendered {got1!r}, expected {want1!r} -- the PDF engine "
        "is not drawing the policy's decision"
    )
    assert got2 == want2, (
        f"row 2 rendered {got2!r}, expected {want2!r}. A one-cell left shift "
        "here means the continuation row's leading blank cell was stripped, so "
        "every character after it lands in the wrong box."
    )
    assert got2[0] == " ", (
        "fixture assumption changed: the 14th character of the decided string "
        "is no longer the space between two words, so this test no longer "
        "covers the leading-blank-cell case. Pick another value."
    )
    assert (got1 + got2).rstrip() == expected.rstrip(), (
        f"the two rows do not reconstruct the decided string: "
        f"{(got1 + got2)!r} != {expected!r}"
    )


def test_the_designer_preview_consumes_the_server_decision_rather_than_reslicing():
    """Guard the third renderer against reintroducing its own ladder.

    This one is unavoidably about the TSX source -- the browser is exercised
    separately by the smoke test -- so it is written to be specific: the comb
    branch must USE the ``combRow`` prop, and must not contain a second
    truncation ladder. A generic substring check would pass on a comment.
    """
    src = (REPO_ROOT / "app" / "frontend" / "src" / "pages" / "designer"
           / "FieldMarker.tsx").read_text(encoding="utf-8")
    # Strip comments so prose cannot satisfy any assertion below.
    code = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    code = re.sub(r"^\s*//.*$", "", code, flags=re.M)

    assert "combRow" in code, "FieldMarker no longer receives the server decision"
    assert re.search(r"chars\s*=\s*combRow\s*!==\s*null", code), (
        "the comb branch must render the server-decided row; if it slices the "
        "value itself again the preview will drift from the PDF"
    )
    for invented in ATTESTED_ABBREVIATIONS:
        assert invented not in code, (
            f"{invented!r} appears in FieldMarker.tsx -- the abbreviation table "
            "is being reimplemented client-side, which is how the three "
            "renderers diverged before"
        )


def test_the_overlay_payload_exposes_the_decision_to_the_browser():
    """The API contract the designer depends on."""
    src = (REPO_ROOT / "app" / "api" / "endpoints" / "documents.py").read_text(
        encoding="utf-8")
    code = re.sub(r"^\s*#.*$", "", src, flags=re.M)
    assert '"comb_row"' in code and '"comb_fit"' in code, (
        "preview-data must ship the decided row and verdict, or the designer "
        "has to recompute them"
    )
    assert "resolve_comb_row" in code


# ===========================================================================
# The real template, and the real data
# ===========================================================================
def test_the_shipped_oep_template_still_has_a_26_character_address_comb():
    """Pin the measurement every threshold in this policy is based on.

    If someone adds a third row or changes cell_count, this fails and the
    documented 26 / 83.4% figures get revisited rather than silently rotting.
    """
    geom = json.loads((REPO_ROOT / "app" / "data" / "template_geometry.json")
                      .read_text(encoding="utf-8"))
    oep = geom.get("OEP Form")
    assert oep, "OEP Form missing from the template geometry snapshot"
    rows = comb_rows_from_fields(oep["fields"], "address", page=1)
    assert rows, "the OEP address comb has no char_cells rows"
    capacity = comb_capacity_for_key(rows)
    assert capacity == OEP_ADDRESS_CAPACITY, (
        f"the OEP address comb now holds {capacity} characters, not "
        f"{OEP_ADDRESS_CAPACITY}. The policy still works, but the documented "
        "distribution (83.4% over capacity) was measured against 26 -- "
        "re-measure and update PROJECT_DOCUMENTATION.md §4.10.3."
    )


def test_the_full_name_comb_is_also_covered_by_the_policy():
    """Address is not the only multi-row comb: full_name is 14 + 14 = 28."""
    geom = json.loads((REPO_ROOT / "app" / "data" / "template_geometry.json")
                      .read_text(encoding="utf-8"))
    rows = comb_rows_from_fields(geom["OEP Form"]["fields"], "full_name", page=1)
    assert comb_capacity_for_key(rows) == 28
    fit = fit_comb_text("MUHAMMAD ABDUL REHMAN KHAN JADOON", 28)
    assert fit.review_required is True
    assert fit.printed and len(fit.printed) <= 28


@pytest.mark.parametrize("capacity", [13, 26, 28, 40])
def test_the_policy_never_returns_more_than_capacity(capacity):
    """The invariant, over a wide spread of inputs.

    Whatever the ladder decides, the printed form fits. If this can be broken
    the form overprints its own boxes.
    """
    samples = [
        "", "A", "A B", "A" * capacity, "A" * (capacity + 1),
        REAL_LONG_ADDRESS,
        "VILLAGE MOHALLAH POST STREET TEHSIL DISTRICT NAROWAL PUNJAB",
        "X" * 200,
        "  padded   with    spaces  ",
        "123-456-789",
    ]
    for value in samples:
        fit = fit_comb_text(value, capacity)
        assert len(fit.printed) <= capacity, (
            f"{value!r} produced {len(fit.printed)} characters for a "
            f"{capacity}-cell comb"
        )
        assert fit.fits or fit.review_required, (
            f"{value!r} -> status {fit.status!r} is neither acceptable nor "
            "flagged; a third state would be invisible to the operator"
        )
