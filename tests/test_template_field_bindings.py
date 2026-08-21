"""Guards for the template geometry audit's "unbound field" axis.

WHY THIS FILE EXISTS
--------------------
Section C of the Allied Bank Deposit Form printed a blank "Address" row for
this portal's entire life (fixed in revision 7f1b5c3e9a48). The existing
tooling could not have caught it, and the reason generalises:

  * ``tests/test_background_geometry.py`` checks the background scans.
  * ``scripts/audit_template_geometry.py`` axes 1 and 2 check aspect ratio and
    off-page boxes.

All three can only inspect fields that **exist and have coordinates**. A row
with no field at all, or a field bound to a key nothing resolves, passes every
one of them trivially -- it is simply invisible to a geometry check. So a whole
class of "the form prints an empty box where the bank expects a value" defects
had no guard at all.

Axis 3 of the audit closes that gap by resolving each template's fields through
the real overlay engine. This file guards the guard.

WHAT IS BEING PINNED
--------------------
  1. **The live templates are clean.** No field on any shipped template is
     bound to a key the engine cannot produce, except the ones explicitly
     documented in ``KNOWN_UNBOUND_FIELDS``.

  2. **The axis actually fires.** A test that only ever asserts "no findings"
     is indistinguishable from a broken detector, so a deliberately bogus
     binding is injected and the audit is required to report it. This is the
     mutation test, built in permanently rather than run once by hand.

  3. **The exception list stays honest.** Every entry must carry a real reason
     and must still correspond to a field that exists -- otherwise the list
     becomes a place where defects go to be forgotten.
"""

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

pytest.importorskip("PIL", reason="the audit needs Pillow to size the scans")

from scripts.audit_template_geometry import (  # noqa: E402
    KNOWN_UNBOUND_FIELDS,
    NON_TEXT_FIELD_TYPES,
    audit,
)

#: Marker used on every throwaway row this module creates, so teardown can find
#: and remove exactly its own records and nothing else.
PROBE_TAG = "Audit Probe (test_template_field_bindings)"


@pytest.fixture(scope="module")
def _a_record_exists():
    """Seed a FULLY-RELATED record chain so axis 3 can actually run.

    WHY THE WHOLE CHAIN, NOT JUST A CANDIDATE
    -----------------------------------------
    ``_resolved_context()`` deliberately returns ``None`` -- "cannot judge, stay
    silent" -- when the database holds no Candidate/Demand row, because
    reporting every field as unbound on an empty DB would be pure noise. That is
    correct behaviour, but it makes axis 3 a **no-op** on the empty pytest
    database, which silently neuters every mutation test in this file: they see
    ``[]`` and cannot tell "detector broken" from "nothing to detect". A
    deliberate mutation (emptying ``KNOWN_UNBOUND_FIELDS`` entirely) was
    verified to slip through undetected for exactly this reason.

    The first attempt at a fix seeded a bare ``Candidate``, and that was NOT
    enough. A large family of overlay keys is *demand-derived* -- it only
    materialises when the candidate has an assignment reaching a Demand:

        visa_number, bataka_number, visa_issue_date_hijri, sponsor_name,
        sponsor_name_arabic, sponsor_phone, country

    (see ``overlay_engine.resolve_overlay_fields`` sections 2 and 5). With an
    unassigned candidate the resolver produced a short key set, so nine fields
    on the OEP and Karachi/Islamabad visa templates were reported as unbound --
    false positives that made the headline assertion fail for the wrong reason.

    So the chain must be complete:

        Client -> Demand -> JobCategory -> CandidateAssignment <- Candidate

    Measured with the full chain: 284 resolved keys and zero unbound findings,
    versus a short set and ten false findings with a bare candidate. This is
    also not merely a test artefact -- 133 of 2675 live candidates have no
    assignment, so a real operator resolving one of those would have seen the
    same phantom defects.

    Module-scoped and depended upon by ``report`` so the headline sweep and the
    mutation tests all observe the same, fully-resolvable database.
    """
    from app.db.session import SessionLocal
    from app.models import (
        Candidate,
        CandidateAssignment,
        Client,
        Demand,
        JobCategory,
    )

    db = SessionLocal()
    created_ids: dict[str, int] = {}
    try:
        client = Client(company_name=f"{PROBE_TAG} Sponsor Co")
        db.add(client)
        db.flush()

        demand = Demand(
            file_number="AUDIT-PROBE-0001",
            client_id=client.id,
            # Every demand-derived key the visa/OEP templates bind to must be
            # non-empty here: a NULL column still yields the key (the resolver
            # coerces to ""), but populating them keeps the fixture honest about
            # what a real assigned candidate looks like.
            visa_number="VP0000001",
            bataka_number="BP0000001",
            visa_issue_date_hijri="1447/01/01",
            sponsor_name=f"{PROBE_TAG} Sponsor",
            sponsor_phone="+966500000000",
            country="Saudi Arabia",
        )
        db.add(demand)
        db.flush()

        job_category = JobCategory(demand_id=demand.id, trade="Mason")
        db.add(job_category)
        db.flush()

        candidate = Candidate(
            full_name=f"{PROBE_TAG} Candidate",
            cnic="3410100000001",
            passport_no="ZZ0000001",
        )
        db.add(candidate)
        db.flush()

        assignment = CandidateAssignment(
            candidate_id=candidate.id,
            job_category_id=job_category.id,
        )
        db.add(assignment)
        db.commit()

        created_ids = {
            "assignment": assignment.id,
            "candidate": candidate.id,
            "job_category": job_category.id,
            "demand": demand.id,
            "client": client.id,
        }
    finally:
        db.close()

    yield created_ids

    # Deleted child-first so foreign keys never block the teardown.
    from app.models import (
        Candidate,
        CandidateAssignment,
        Client,
        Demand,
        JobCategory,
    )
    db = SessionLocal()
    try:
        for model, key in (
            (CandidateAssignment, "assignment"),
            (Candidate, "candidate"),
            (JobCategory, "job_category"),
            (Demand, "demand"),
            (Client, "client"),
        ):
            row_id = created_ids.get(key)
            if row_id is None:
                continue
            row = db.query(model).filter(model.id == row_id).first()
            if row is not None:
                db.delete(row)
        db.commit()
    finally:
        db.close()


@pytest.fixture(scope="module")
def report(_a_record_exists):
    """The audit report, computed against a database where axis 3 can resolve.

    The dependency on ``_a_record_exists`` is load-bearing, not cosmetic:
    without it ``_resolved_context()`` returns ``None``, axis 3 stays silent,
    and every assertion built on this fixture passes vacuously.
    """
    return audit()


# ===========================================================================
# 1. The shipped templates are clean
# ===========================================================================

def test_no_template_field_is_bound_to_an_unresolvable_key(report):
    """The headline assertion: every text field on every template must be able
    to render a value.

    An empty *value* is not a failure -- ``candidates.email`` and
    ``candidates.passport_issue_place`` are wired correctly and merely
    unpopulated, which is data entry. A missing *key* is a failure, because no
    amount of data entry can ever fill it.
    """
    unbound = [f for f in report["findings"] if f["kind"] == "unbound_field"]
    assert not unbound, (
        "these fields can never render a value:\n"
        + "\n".join(
            f"  template {f['template_id']} {f['template']!r}: "
            f"{f['field']!r} (label {f.get('label')!r})"
            for f in unbound
        )
        + "\nEither bind each to a real overlay-engine key, or add it to "
          "KNOWN_UNBOUND_FIELDS with the reason it is blank by design."
    )


def test_the_audit_examined_something(report):
    """Guards against the whole report silently becoming empty -- a resolver
    import error would otherwise turn every assertion here green."""
    assert report["templates_checked"] >= 5, (
        f"only {report['templates_checked']} templates were examined; the "
        f"audit is not seeing the shipped template set"
    )


def test_axis_three_actually_resolved_a_context(_a_record_exists):
    """The load-bearing precondition for every "no unbound fields" assertion.

    ``_resolved_context()`` returning ``None`` is a legitimate, deliberate
    "stay silent" -- but it also makes the headline sweep pass vacuously. That
    is exactly how a real mutation (emptying ``KNOWN_UNBOUND_FIELDS``) slipped
    through undetected, so silence is pinned as a failure here rather than
    trusted.

    The key-count floor is well below the 284 keys measured with the seeded
    chain, so ordinary schema growth will not make it brittle, while a resolver
    that collapses to a handful of keys still trips it.
    """
    from app.db.session import SessionLocal
    from app.models.document import DocumentTemplate
    from scripts.audit_template_geometry import _resolved_context

    db = SessionLocal()
    try:
        templates = db.query(DocumentTemplate).all()
        if not templates:
            pytest.skip("no templates in this database")

        resolved_counts = {}
        for template in templates:
            ctx = _resolved_context(db, template)
            resolved_counts[template.name] = None if ctx is None else len(ctx)
    finally:
        db.close()

    unresolved = [name for name, n in resolved_counts.items() if n is None]
    assert not unresolved, (
        "the overlay engine resolved NOTHING for these templates, so axis 3 "
        f"was a no-op for them and cannot have detected anything: {unresolved}. "
        "The _a_record_exists fixture must seed a record reachable from each "
        "template's data_source."
    )
    thin = {name: n for name, n in resolved_counts.items() if n < 100}
    assert not thin, (
        f"suspiciously few resolved keys {thin} -- the seeded record chain is "
        f"probably incomplete (a candidate with no demand assignment resolves "
        f"only a short key set, which produces phantom 'unbound' findings)"
    )


def test_demand_derived_keys_resolve_for_an_assigned_candidate(_a_record_exists):
    """Pin the specific key family that made the first fixture attempt wrong.

    These seven keys come from the Demand reached through
    ``CandidateAssignment -> JobCategory -> Demand``, not from the candidate
    row. They were reported as unbound on nine template fields until the
    fixture seeded the whole chain. Naming them individually means a future
    refactor that drops one is reported as itself instead of as an anonymous
    count change.
    """
    from app.db.session import SessionLocal
    from app.models.document import DocumentTemplate
    from scripts.audit_template_geometry import _resolved_context

    demand_derived = (
        "visa_number",
        "bataka_number",
        "visa_issue_date_hijri",
        "sponsor_name",
        "sponsor_name_arabic",
        "sponsor_phone",
        "country",
    )

    db = SessionLocal()
    try:
        template = (
            db.query(DocumentTemplate)
            .filter(DocumentTemplate.name == "Visa Application Form - Karachi")
            .first()
        )
        if template is None:
            pytest.skip("Karachi visa template is not present in this database")
        ctx = _resolved_context(db, template)
    finally:
        db.close()

    assert ctx is not None, "axis 3 resolved nothing; see the fixture docstring"
    missing = [k for k in demand_derived if k not in ctx]
    assert not missing, (
        f"these demand-derived keys no longer resolve: {missing}. Every visa "
        f"and OEP field bound to them would print a blank box."
    )


def test_the_allied_bank_address_row_stays_bound():
    """The specific defect that motivated this axis.

    Pinned separately from the general sweep so that deleting the field is
    reported as itself, not as an anonymous count change.
    """
    from app.db.session import SessionLocal
    from app.models.document import DocumentField, DocumentTemplate

    db = SessionLocal()
    try:
        template = (
            db.query(DocumentTemplate)
            .filter(DocumentTemplate.name == "Allied Bank Deposit Form")
            .first()
        )
        if template is None:
            pytest.skip("Allied Bank template is not present in this database")

        keys = {
            f.field_key
            for f in db.query(DocumentField).filter(
                DocumentField.template_id == template.id
            )
        }
    finally:
        db.close()

    assert "customer_address" in keys, (
        "Section C's Address row has lost its binding -- the form would print "
        "a blank mandatory field again (see revision 7f1b5c3e9a48)"
    )


# ===========================================================================
# 2. The axis actually fires
# ===========================================================================

def test_the_audit_reports_a_deliberately_broken_binding(monkeypatch, _a_record_exists):
    """Prove the detector is not vacuous.

    A bogus key is injected into the resolved context lookup -- specifically by
    shrinking what the engine appears to resolve -- so a real field becomes
    unresolvable without touching the database. If the audit stays quiet here,
    every other assertion in this file is worthless.
    """
    import scripts.audit_template_geometry as audit_mod

    real_resolver = audit_mod._resolved_context

    def _lying_resolver(db, template):
        resolved = real_resolver(db, template)
        if resolved is None:
            return None
        # Drop a key that a shipped template is definitely bound to.
        return {k: v for k, v in resolved.items() if k != "full_name"}

    monkeypatch.setattr(audit_mod, "_resolved_context", _lying_resolver)

    findings = [
        f for f in audit_mod.audit()["findings"]
        if f["kind"] == "unbound_field"
    ]
    assert findings, (
        "the audit reported nothing after 'full_name' was made unresolvable -- "
        "axis 3 is not actually checking anything (if _resolved_context() is "
        "returning None, the database has no candidate/demand record and the "
        "_a_record_exists fixture is not doing its job)"
    )
    assert any(f["field"] == "full_name" for f in findings), (
        f"expected a finding for 'full_name', got "
        f"{sorted({f['field'] for f in findings})}"
    )


def test_an_unbound_finding_is_a_defect_not_an_advisory(monkeypatch, _a_record_exists):
    """Severity matters: the release gate exits non-zero on defects only, so an
    'advisory' here would let a blank mandatory field ship."""
    import scripts.audit_template_geometry as audit_mod

    real_resolver = audit_mod._resolved_context
    monkeypatch.setattr(
        audit_mod, "_resolved_context",
        lambda db, t: (
            None if (r := real_resolver(db, t)) is None
            else {k: v for k, v in r.items() if k != "full_name"}
        ),
    )
    findings = [
        f for f in audit_mod.audit()["findings"]
        if f["kind"] == "unbound_field"
    ]
    assert findings
    assert all(f["severity"] == "defect" for f in findings)


# ===========================================================================
# 3. The exception list stays honest
# ===========================================================================

def test_every_documented_exception_has_a_real_reason():
    """An exception list without reasons is just a suppression list."""
    for (template_name, field_key), reason in KNOWN_UNBOUND_FIELDS.items():
        assert template_name and field_key
        assert isinstance(reason, str) and len(reason) >= 40, (
            f"({template_name!r}, {field_key!r}) is exempted with only "
            f"{reason!r} -- state why the blank is correct, so the next reader "
            f"can re-judge it"
        )


def test_no_exception_is_stale():
    """If the field no longer exists, the entry must go: a lingering exemption
    would silently pre-approve a future field that reuses the same key."""
    from app.db.session import SessionLocal
    from app.models.document import DocumentField, DocumentTemplate

    db = SessionLocal()
    try:
        live = {
            (t.name, f.field_key)
            for t, f in db.query(DocumentTemplate, DocumentField).filter(
                DocumentField.template_id == DocumentTemplate.id
            )
        }
    finally:
        db.close()

    if not live:
        pytest.skip("no templates in this database to compare against")

    stale = [pair for pair in KNOWN_UNBOUND_FIELDS if pair not in live]
    assert not stale, (
        f"KNOWN_UNBOUND_FIELDS exempts fields that no longer exist: {stale}. "
        f"Remove them, or a future field reusing the key inherits the "
        f"exemption without review."
    )


def test_the_exception_list_is_small():
    """A soft ceiling, deliberately low. If exemptions are piling up, the right
    response is to fix bindings, not to keep widening the allowlist."""
    assert len(KNOWN_UNBOUND_FIELDS) <= 5, (
        f"{len(KNOWN_UNBOUND_FIELDS)} unbound fields are exempted; this list "
        f"is meant for genuine blank-by-design cases only"
    )


def test_non_text_types_are_excluded_from_the_binding_check():
    """These draw from static_value or their own generator, so they have no
    resolved key and would otherwise flood the report with false positives."""
    for expected in ("checkbox", "barcode", "image", "trade_table"):
        assert expected in NON_TEXT_FIELD_TYPES


# ===========================================================================
# 2. Collectability: a bound field the UI cannot fill is blank by construction
# ===========================================================================
# WHY THIS SECTION EXISTS (measured 2026-08-12)
#
# The checks above prove a template's fields resolve to keys the engine can
# produce. That is necessary but NOT sufficient. `email` resolved perfectly:
# the column existed on Candidate, the overlay engine produced the key, the
# geometry was correct, and the candidate detail panel even displayed it. It
# printed blank on every single form anyway -- for all 2,675 records -- because
# there was no <input> for it ANYWHERE in the candidate wizard. It could not be
# typed in, so it could never have a value.
#
# That defect is invisible to every geometry and binding check by design: the
# field is perfectly wired, the DATA ENTRY PATH is the missing half. A "0%
# populated" column also cannot be distinguished from "operators haven't got
# to it yet" by looking at data alone -- only the presence or absence of an
# input tells you which. So the contract is structural, not statistical:
#
#   Every Candidate column bound on a shipped template must appear in the
#   wizard's STEP_FIELDS map.
#
# STEP_FIELDS is the authoritative slice list: the wizard PATCHes each step's
# keys from it, and finalize() flat-merges those slices into the Candidate row.
# A key absent from it is never sent, whatever inputs exist on screen.

WIZARD_TSX = os.path.join(
    REPO_ROOT, "app", "frontend", "src", "components", "AssignWizard.tsx"
)


def _wizard_step_field_keys() -> set:
    """Parse STEP_FIELDS out of AssignWizard.tsx.

    Deliberately reads the STEP_FIELDS map rather than scanning the whole file
    for `form.<key>`: a rendered input whose key is not in STEP_FIELDS is never
    transmitted, so matching on the inputs alone would pass a field that still
    silently discards what the operator types.
    """
    import re

    with open(WIZARD_TSX, encoding="utf-8") as fh:
        src = fh.read()
    m = re.search(r"const STEP_FIELDS[^=]*=\s*\{(.*?)\n\}", src, re.S)
    assert m, (
        f"STEP_FIELDS map not found in {WIZARD_TSX}. If it was renamed or "
        f"restructured, update this parser -- do not delete the test, or the "
        f"collectability contract silently stops being enforced."
    )
    return set(re.findall(r"'([a-z0-9_]+)'", m.group(1)))


def _candidate_column_names() -> set:
    from app.models.candidate import Candidate

    return {c.name for c in Candidate.__table__.columns}


def test_every_candidate_column_bound_on_a_template_is_collectable():
    """A bound Candidate field with no wizard input prints blank forever."""
    from app.db.session import SessionLocal
    from app.models.document import DocumentField, DocumentTemplate

    db = SessionLocal()
    try:
        bound = {
            f.field_key
            for _t, f in db.query(DocumentTemplate, DocumentField).filter(
                DocumentField.template_id == DocumentTemplate.id
            )
        }
    finally:
        db.close()

    if not bound:
        pytest.skip("no templates in this database to compare against")

    collectable = _wizard_step_field_keys()
    # Only Candidate columns are in scope. Client/demand/agency keys are filled
    # elsewhere (client forms, demand wizard, company settings), so requiring
    # them here would be wrong.
    in_scope = bound & _candidate_column_names()
    missing = sorted(in_scope - collectable)

    assert not missing, (
        f"These Candidate columns are bound on a shipped template but have no "
        f"entry in the wizard's STEP_FIELDS, so an operator cannot supply them "
        f"and they print blank on every document: {missing}. Add an input and "
        f"register the key in STEP_FIELDS, or unbind the field from the "
        f"template. This is exactly how `email` stayed empty for 2,675 records."
    )


def test_the_collectability_check_actually_fires():
    """Mutation guard. A test that only ever asserts an empty list is
    indistinguishable from a broken parser, so prove the detector reports a
    field that is genuinely missing from STEP_FIELDS."""
    collectable = _wizard_step_field_keys()
    bogus = "definitely_not_a_wizard_field_xyz"
    assert bogus not in collectable
    in_scope = {bogus, "full_name"}
    missing = sorted(in_scope - collectable)
    assert missing == [bogus], (
        "the set difference that drives the collectability check did not "
        "isolate the uncollectable field"
    )


def test_the_wizard_parser_finds_a_realistic_number_of_fields():
    """Pins the parser itself. If a refactor makes the regex match nothing (or
    the whole file), the check above would pass vacuously or fail noisily; both
    are worse than a clear failure here."""
    keys = _wizard_step_field_keys()
    assert len(keys) >= 40, (
        f"only {len(keys)} keys parsed from STEP_FIELDS -- the regex has "
        f"probably stopped matching the real map"
    )
    for expected in ("full_name", "father_name", "cnic", "passport_no", "email"):
        assert expected in keys, f"{expected!r} missing from parsed STEP_FIELDS"
