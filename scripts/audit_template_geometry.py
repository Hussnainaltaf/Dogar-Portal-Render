#!/usr/bin/env python3
"""Audit the Overlay Engine's geometry against LIVE template data.

WHY THIS EXISTS
---------------
``tests/test_background_geometry.py`` already guards the background scans, but it
measures every scan against a hardcoded ``A4_ASPECT`` constant and asserts only
that ``DocumentTemplate.page_width``'s *column default* is still 595. It never
looks at what page size the template rows actually carry.

That blind spot let the v1.1.0 defect come back through a different axis. The
original fix cropped ``allied_bank_form7.jpg`` from Letter-shaped 1807x2339 to a
true-A4 1653x2339 so it would stop being stretched on an A4 page. The image is
still correctly A4 today -- but its template row now says Letter (612x792), so
the same scan is stretched again, by +9.34% instead of -8.53%. The suite stayed
green the whole time, because the image passes and the column default passes.

Two failure modes are therefore audited here, both against each template's OWN
geometry rather than a constant:

  1. ASPECT  -- scan aspect vs that template's page aspect. `bg_fit='fill'`
     stretches the artwork to the page box while overlaid text is placed at its
     literal coordinate, so a mismatch slides the printed boxes out from under
     their values, with the error growing away from the origin.

  2. OFF-PAGE -- any field box falling outside its page. reportlab clips it in
     silence, so the value just never appears.

  3. UNBOUND  -- any field bound to a key the overlay engine does not produce.
     Added after Section C of the Allied Bank form was found printing a blank
     "Address" row for this portal's entire life. Axes 1 and 2 could never have
     caught it, because both can only inspect fields that EXIST -- a row with
     no field at all, or a field pointing at a key nobody resolves, passes
     every geometry check trivially. A blank mandatory field on a bank deposit
     form is a form the bank can reject, so it is reported as a defect.

     Genuinely-blank-by-design fields are listed in KNOWN_UNBOUND_FIELDS with a
     reason, rather than being silently tolerated.

Run:  python scripts/audit_template_geometry.py [--json]
Exit: 0 clean, 1 if any defect was found (usable as a release gate).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.session import SessionLocal  # noqa: E402
from app.models.document import DocumentField, DocumentTemplate  # noqa: E402

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    print("Pillow is required: pip install Pillow", file=sys.stderr)
    # `from None`: the ImportError traceback adds nothing for an operator who
    # just needs to install Pillow, and chaining it makes the actionable message
    # above harder to spot.
    raise SystemExit(2) from None

BG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "app", "static", "pdf_backgrounds",
)

# Same tolerance the scan-level test uses, for one consistent house rule.
ASPECT_TOLERANCE_PCT = 2.0

# Field types that legitimately carry no resolved text value, so the unbound
# check must skip them: they draw from `static_value`, from their own generator,
# or from a separate code path entirely.
NON_TEXT_FIELD_TYPES = frozenset({
    "checkbox", "barcode", "image", "trade_table", "static", "qrcode",
})

# Fields that resolve to no key ON PURPOSE. Listed with the reason so the audit
# stays quiet about them without going quiet about the whole class.
#
# (template name, field key) -> why it is allowed to be blank
KNOWN_UNBOUND_FIELDS: dict[tuple[str, str], str] = {
    (
        "Visa Application Form - Islamabad", "previous_nationality"
    ): (
        "'Previous Nationality' (الجنسية السابقة) on the Saudi visa form is only "
        "filled for naturalised applicants. Every candidate in this portal is "
        "Pakistani by birth, so the correct output is an empty box -- verified "
        "in a rendered PDF: the field draws nothing, with no placeholder leak. "
        "There is no candidates.previous_nationality column and no resolver key "
        "by design; adding one would invite wrong data onto an embassy form."
    ),
}


def _scan_size(background_image: str | None):
    """Resolve a template's background to (width_px, height_px), or None."""
    if not background_image:
        return None
    path = os.path.join(BG_DIR, os.path.basename(str(background_image)))
    if not os.path.exists(path):
        return None
    with Image.open(path) as im:
        return im.size


#: How many records to resolve when building the reachable-key set. Small
#: enough to stay fast on a 2675-candidate database, large enough that at least
#: one record with a demand assignment is virtually always included.
SAMPLE_RECORDS = 25


def _resolved_context(db, template) -> dict | None:
    """The UNION of keys the overlay engine can produce for this template.

    Returns None -- meaning "cannot judge, stay silent" -- when there is no
    record of the template's data source to resolve against. An audit that
    reported every field as unbound on an empty database would be worse than no
    audit: operators would learn to ignore it.

    WHY A UNION AND NOT ONE RECORD (fixed 2026-08-03)
    -------------------------------------------------
    The first version resolved exactly one record (``ORDER BY id LIMIT 1``) and
    treated its key set as definitive. That produced FALSE POSITIVES, because a
    large family of keys only materialises when the candidate has a demand
    assignment -- ``visa_number``, ``sponsor_name``, ``sponsor_phone``,
    ``sponsor_name_arabic``, ``visa_issue_date_hijri``, ``bataka_number``,
    ``country`` and more are all populated from the linked Demand
    (overlay_engine.py L310-327).

    Caught by the test suite rather than by inspection: the audit was clean
    against the live database (candidate #1 has an assignment) but reported 9
    unbound fields against a freshly seeded one (probe candidate has none).
    Both answers came from the same correct code -- the sample was the problem.
    It is not merely a test artifact either: 133 of the 2675 live candidates
    have no assignment, so a run that happened to pick one of those would have
    reported the same 9 false defects to the operator.

    Resolving a sample and unioning the keys asks the question the axis actually
    means: "is there ANY record for which this field could render?" A field is
    only reported when no sampled record can produce it, which is the condition
    that makes a blank box unavoidable rather than merely unpopulated.
    """
    from app.models import Candidate, Demand  # local: keeps import cost off --json
    from app.services.overlay_engine import (
        resolve_all_related_data_for_record,
    )

    source = (template.data_source or "candidate").lower()
    model = Demand if source == "demand" else Candidate
    try:
        records = db.query(model).order_by(model.id).limit(SAMPLE_RECORDS).all()
    except Exception:
        return None
    if not records:
        return None

    merged: dict = {}
    resolved_any = False
    for record in records:
        try:
            resolved = resolve_all_related_data_for_record(db, source, record.id)
        except Exception:
            # A resolver failure is a different defect with its own surface (the
            # generate endpoint would 500); do not misreport it as unbound
            # fields. Skip this record and keep sampling.
            continue
        if not isinstance(resolved, dict):
            continue
        resolved_any = True
        for key, value in resolved.items():
            # Prefer a non-empty value so callers can still distinguish
            # "resolvable but blank" from "resolvable and populated".
            if key not in merged or (not merged[key] and value):
                merged[key] = value

    return merged if resolved_any else None


def audit() -> dict:
    db = SessionLocal()
    try:
        templates = db.query(DocumentTemplate).order_by(DocumentTemplate.id).all()
        findings: list[dict] = []

        for t in templates:
            page_w = float(t.page_width or 595.0)
            page_h = float(t.page_height or 842.0)

            # --- 1. aspect, measured against THIS template's page -------------
            size = _scan_size(t.background_image)
            if t.background_image and size is None:
                findings.append({
                    "template_id": t.id, "template": t.name, "kind": "missing_background",
                    "detail": f"background_image={t.background_image!r} does not resolve under "
                              f"{BG_DIR} -- renders a blank page with floating text",
                })
            elif size:
                sw, sh = size
                scan_ar = sw / sh
                page_ar = page_w / page_h
                dev = (page_ar / scan_ar - 1.0) * 100.0
                if abs(dev) > ASPECT_TOLERANCE_PCT:
                    findings.append({
                        "template_id": t.id, "template": t.name, "kind": "aspect_mismatch",
                        "scan": f"{sw}x{sh}", "page": f"{page_w:.0f}x{page_h:.0f}",
                        "x_distortion_pct": round(dev, 2),
                        "detail": f"scan aspect {scan_ar:.5f} vs page aspect {page_ar:.5f} -- "
                                  f"bg_fit='fill' stretches the artwork {dev:+.2f}% horizontally "
                                  f"while overlaid text stays true",
                    })

            # --- 2. fields outside the page box ------------------------------
            #
            # Severity is split deliberately, because these are not the same bug
            # and conflating them cries wolf:
            #
            #   origin_off_page  -- the draw origin (x, y) itself is outside the
            #       page. reportlab has nowhere to start, so the value is
            #       ENTIRELY absent. This is the real defect.
            #
            #   box_overflows    -- the origin is on-page but the box extends
            #       past an edge. `width` is a wrap/clip width, not the glyph
            #       extent, so the value still prints and only a long value
            #       risks truncation. Reported as advisory, not as data loss.
            #
            # Getting this wrong in the other direction would have been worse
            # than silence: the first version of this script reported template
            # 1's `cnic` (x=388.8, w=260 on a 612pt page) as "never appears",
            # which is false -- it prints fine, as the live print preview shows.
            fields = db.query(DocumentField).filter(DocumentField.template_id == t.id).all()
            for f in fields:
                x, y = float(f.x or 0), float(f.y or 0)
                w, h = float(f.width or 0), float(f.height or 0)
                box = f"x={x:.1f} y={y:.1f} w={w:.1f} h={h:.1f}"
                page = f"{page_w:.0f}x{page_h:.0f}"

                if x < 0 or y < 0 or x > page_w or y > page_h:
                    findings.append({
                        "template_id": t.id, "template": t.name, "kind": "origin_off_page",
                        "field": f.field_key or f.label, "box": box, "page": page,
                        "severity": "defect",
                        "detail": "the draw origin is outside the page, so reportlab clips the "
                                  "field entirely and the value NEVER appears in the document",
                    })
                elif x + w > page_w + 0.5 or y + h > page_h + 0.5:
                    findings.append({
                        "template_id": t.id, "template": t.name, "kind": "box_overflows_edge",
                        "field": f.field_key or f.label, "box": box, "page": page,
                        "severity": "advisory",
                        "detail": "origin is on-page so the value prints; the box extends past the "
                                  "edge, so only an unusually long value risks being truncated",
                    })

            # --- 3. fields bound to a key nothing resolves --------------------
            #
            # Resolved against a REAL record through the overlay engine, not
            # against a hardcoded key list: a list would drift out of date the
            # moment the engine gained or lost a key, which is precisely the
            # kind of silent divergence this script exists to catch.
            #
            # A missing KEY is the defect. An EMPTY VALUE is not reported: the
            # candidate columns `email` and `passport_issue_place` are wired
            # correctly and simply unpopulated across all records, which is a
            # data-entry matter and not something a release gate should block.
            resolved = _resolved_context(db, t)
            if resolved is not None:
                for f in fields:
                    key = f.field_key or ""
                    if not key or key.startswith("__"):
                        continue
                    if (f.field_type or "text") in NON_TEXT_FIELD_TYPES:
                        continue
                    if key in resolved:
                        continue
                    if (t.name, key) in KNOWN_UNBOUND_FIELDS:
                        continue
                    findings.append({
                        "template_id": t.id, "template": t.name,
                        "kind": "unbound_field",
                        "field": key, "label": f.label,
                        "severity": "defect",
                        "detail": "no overlay-engine key by this name, so the field can NEVER "
                                  "render a value regardless of the record -- it prints as a "
                                  "blank box. Either bind it to a real key or add it to "
                                  "KNOWN_UNBOUND_FIELDS with a reason",
                    })

        return {"templates_checked": len(templates), "findings": findings}
    finally:
        db.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    result = audit()
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        n = result["templates_checked"]
        fs = result["findings"]
        print(f"Overlay Engine geometry audit -- {n} template(s) checked\n")
        if not fs:
            print("  OK: every scan matches its own page aspect and every field is on-page.")
        else:
            for f in fs:
                head = f"  [{f['kind']}] template {f['template_id']} {f['template']!r}"
                print(head)
                for k, v in f.items():
                    if k in ("kind", "template_id", "template"):
                        continue
                    print(f"      {k}: {v}")
                print()
            print(f"{len(fs)} finding(s).")
    # Only real defects gate a release. Advisory box-overflows are reported for
    # a human to look at but must not block, or the gate gets switched off.
    defects = [f for f in result["findings"] if f.get("severity") != "advisory"]
    if not args.json and result["findings"]:
        print(f"({len(defects)} defect(s), {len(result['findings']) - len(defects)} advisory)")
    return 1 if defects else 0


if __name__ == "__main__":
    raise SystemExit(main())
