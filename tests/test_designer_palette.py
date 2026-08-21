"""Completeness guards for the Template Designer field palette.

BACKGROUND (audit 2026-07-30). An operator reported that the Documents
Coordinate Engine was missing data fields -- specifically "sponsor id" and
"bataqa no." -- and that some fields dropped onto a form printed nothing.

Both symptoms had the same cause: the set of DRAGGABLE fields (a hand-written
list in ``documents.designer_palette``) and the set of RESOLVABLE fields (a
hand-written list of short aliases in ``overlay_engine.resolve_overlay_fields``)
were maintained independently and had drifted apart in *both* directions:

  * 145 columns resolved fine but were not offered in the palette, so they
    could not be placed on a form at all. ``Demand.bataka_number`` and
    ``Client.sponsor_id`` -- the two the operator named -- were among them.
  * 60 palette entries resolved to nothing, so a field dropped from them
    rendered permanently blank with no error anywhere.

Both lists are now derived from ``overlay_engine.ALIAS_PRECEDENCE`` /
``NO_ALIAS_COLUMNS`` via ``resolvable_short_keys()``. These tests fail if that
single source of truth is bypassed again.
"""

import pytest

from app.services import overlay_engine
from app.services.overlay_engine import (
    ALIAS_PRECEDENCE,
    NO_ALIAS_COLUMNS,
    resolvable_short_keys,
)

# Palette keys that intentionally do not come from a data column: they are
# designer primitives (free-text / checkbox / barcode boxes), computed values,
# or composite widgets. They must never be counted as "unresolvable" fields.
STRUCTURAL_KEYS = {
    "__today__",
    "__now__",
    "__static_text__",
    "__static_checkbox__",
    "__static_barcode__",
    "page_number",
    "trades_table",
    "photo",
    "candidate_photo",
    "document_category",
}


def _full_resolvable_surface():
    """Every key ``resolve_overlay_fields`` can emit when all four related rows
    are present.

    Measuring this with ``candidate=None, demand=None, ...`` is not good enough:
    whole sections of the resolver are skipped when their row is absent (the
    tenant ``customer_*`` block needs a session, the gender/marital/religion
    checkboxes are derived from the candidate, and ``selected_trade_*`` comes
    from the job category). A test that measured the empty case would report ~40
    false "phantom" fields and mask real ones.

    Unsaved ORM instances are enough -- ``extract()`` walks ``__table__.columns``
    and does not need the row to exist in the database.
    """
    from app.db.session import SessionLocal
    from app.models.candidate import Candidate
    from app.models.client import Client
    from app.models.demand import Demand, JobCategory

    db = SessionLocal()
    try:
        return set(
            overlay_engine.resolve_overlay_fields(
                candidate=Candidate(),
                demand=Demand(),
                job_category=JobCategory(),
                client=Client(),
                db=db,
            )
        )
    finally:
        db.close()


def _palette_keys(client, auth_headers, data_source="candidate"):
    resp = client.get(
        f"/api/documents/designer/palette?data_source={data_source}",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    keys = {f["key"] for g in body["groups"] for f in g["fields"]}
    assert keys, "the designer palette returned no fields at all"
    return keys, body


def test_the_two_fields_the_operator_reported_are_draggable(client, auth_headers):
    """Regression guard for the actual report, by name.

    `sponsor_id` (Client.sponsor_id) and `bataka_number` (Demand.bataka_number)
    are real columns carrying real data. They were invisible in the designer, so
    operators faked them with a static text box that then printed the literal
    placeholder text onto the Saudi visa form.
    """
    keys, _ = _palette_keys(client, auth_headers)
    for reported in ("sponsor_id", "bataka_number"):
        assert reported in keys, (
            f"{reported!r} is missing from the designer palette again -- this is "
            "the exact field the operator reported on 2026-07-30"
        )


def test_every_palette_field_can_actually_be_resolved(client, auth_headers):
    """No palette entry may render permanently blank.

    A field offered for drag-and-drop that the resolver never produces is worse
    than a missing field: the operator places it, sees nothing on the printed
    page, and has no way to tell whether the data or the template is at fault.
    """
    keys, _ = _palette_keys(client, auth_headers)
    resolvable = set(resolvable_short_keys()) | _full_resolvable_surface()

    phantom = sorted(k for k in keys if k not in resolvable and k not in STRUCTURAL_KEYS)
    assert not phantom, (
        "these palette fields resolve to nothing and would print blank forever: "
        f"{phantom}"
    )


def test_every_column_of_the_alias_source_models_is_draggable(client, auth_headers):
    """The completeness half of the contract.

    If a column is resolvable it must also be placeable, otherwise we are back
    to the original bug where data existed but could not be put on a form.
    """
    keys, _ = _palette_keys(client, auth_headers)
    missing = sorted(k for k in resolvable_short_keys() if k not in keys)
    assert not missing, (
        "these resolvable fields cannot be dragged onto a template: " f"{missing}"
    )


def test_adding_a_column_to_a_model_makes_it_draggable_automatically():
    """Prove the sweep is genuinely model-derived, not a list that happens to match.

    This is the property that stops the defect recurring: the palette must pick
    up a brand-new column with no edit to the palette code.
    """
    from app.models.demand import Demand

    real = {c.name for c in Demand.__table__.columns}
    swept = resolvable_short_keys()
    # Every non-housekeeping Demand column must be present in the sweep unless a
    # more specific model (Candidate / JobCategory) already claimed that name.
    earlier_models = []
    for prefix, module_path, model_name, _group in overlay_engine.ALIAS_SOURCE_MODELS:
        if prefix == "demand_":
            break
        import importlib

        earlier_models.append(
            getattr(importlib.import_module(module_path), model_name)
        )
    claimed_earlier = {
        c.name for m in earlier_models for c in m.__table__.columns
    }
    for col in real - set(NO_ALIAS_COLUMNS) - claimed_earlier:
        assert col in swept, (
            f"Demand.{col} is a real column but the palette sweep does not "
            "offer it -- the sweep is not deriving from the models"
        )


def test_alias_precedence_puts_the_document_subject_first():
    """`status` on a candidate document must mean the candidate's status.

    Several tables own the same short column names (status, notes, country,
    phone). Precedence must run from the document's subject outwards, or a
    candidate form would silently print the client's status instead.
    """
    assert ALIAS_PRECEDENCE[0] == "candidate_", (
        "candidate_ must be the most specific prefix; otherwise short names like "
        "`status` and `notes` resolve to the wrong table on candidate documents"
    )
    assert ALIAS_PRECEDENCE.index("client_") == len(ALIAS_PRECEDENCE) - 1

    swept = resolvable_short_keys()
    from app.models.candidate import Candidate

    candidate_cols = {c.name for c in Candidate.__table__.columns}
    for shared in ("status", "notes"):
        if shared in candidate_cols and shared in swept:
            assert swept[shared] == "Candidate", (
                f"{shared!r} resolved to the {swept[shared]!r} group -- precedence "
                "has been reordered and candidate documents will print the wrong row"
            )


def test_housekeeping_columns_are_not_offered_as_document_fields():
    """`id`, `created_at` and the audit columns are not document data.

    Aliasing them would let `id` mean four different things depending on
    precedence, and would clutter the palette with values no form ever prints.
    """
    swept = resolvable_short_keys()
    for junk in ("id", "created_at", "updated_at", "created_by_id", "custom_fields"):
        assert junk not in swept, (
            f"{junk!r} leaked into the draggable field set; it must stay in "
            "NO_ALIAS_COLUMNS"
        )


@pytest.mark.parametrize("data_source", ["candidate", "demand", "client"])
def test_the_palette_is_never_empty_for_any_shipped_data_source(
    client, auth_headers, data_source
):
    """Each template data_source must offer a usable palette.

    A designer opened on a demand or client template with an empty palette is
    unusable, and the failure is silent in the UI.
    """
    keys, body = _palette_keys(client, auth_headers, data_source)
    assert len(keys) > 50, (
        f"data_source={data_source!r} offers only {len(keys)} fields, which is "
        "far below the ~150 the engine can resolve"
    )
    groups = [g["group"] for g in body["groups"]]
    assert len(groups) == len(set(groups)), f"duplicate palette groups: {groups}"


def test_palette_entries_are_unique_and_well_formed():
    """Duplicate keys would make the designer's drag source ambiguous."""
    seen_keys = []
    swept = resolvable_short_keys()
    for key, group in swept.items():
        assert key and isinstance(key, str), f"bad palette key {key!r}"
        assert not key.startswith("_"), f"{key!r} looks like a private/structural key"
        assert group, f"{key!r} was swept into an empty group"
        seen_keys.append(key)
    assert len(seen_keys) == len(set(seen_keys))
