#!/usr/bin/env python3
"""Measure the LIVE printed geometry of every char_cells comb in a browser.

WHY THIS SCRIPT EXISTS
----------------------
The document coordinate engine has three independent render paths that must
never disagree about what a template means:

  1. ``app/services/pdf_engine.py``      -- the downloadable PDF (ReportLab)
  2. ``app/api/endpoints/documents.py``  -- the HTML the Print button uses
  3. ``designer/FieldMarker.tsx``        -- what the operator sees on screen

Every geometry defect this engine has ever shipped was a DIVERGENCE between
those paths, and each one survived a green test suite because the tests
inspected source code rather than output. The unit and behavioural tests in
``tests/test_print_geometry.py`` now execute all three paths, but they still
run in Python/node -- not in a browser laying out real CSS.

This script closes that last gap: it drives the real print endpoint in a real
Chromium, reads ``getBoundingClientRect()`` for every rendered character, and
compares each character's centre against the pitch the template declares. It is
how the px-vs-pt unit bug was originally found (points emitted as ``px`` render
at exactly 0.75x, so a 23.4pt CNIC comb came out at 17.55pt and the digits
walked progressively left) -- a defect that is invisible to any test that does
not lay out CSS.

USAGE
-----
    ./venv/bin/python scripts/verify_comb_geometry.py [BASE_URL]

BASE_URL defaults to $PORTAL_URL, else https://localhost:3000.

IMPORTANT -- USE AN HTTPS URL. The ``access_token`` cookie is issued with
secure=True in production mode, so a plain-HTTP localhost origin silently drops
it and every request 401s. In the sandbox, pass the HTTPS service URL.

EXIT STATUS
-----------
0 = every comb's characters are within tolerance (a quarter of the cell pitch,
    the point past which a character is visibly outside its printed box)
1 = at least one comb is mis-positioned, or the run could not be completed

A field whose DATA is empty is reported as ``DATA EMPTY``, not as a geometry
failure: an empty value proves nothing about the geometry either way, and
conflating the two is how an earlier version of this check reported false
failures.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

# CSS px per PDF point. 1px = 1/96in, 1pt = 1/72in.
PT_TO_CSS = 96.0 / 72.0


def _creds() -> tuple[str, str]:
    """Read the bootstrap admin credentials out of .env (never hardcoded)."""
    env = os.path.join(REPO_ROOT, ".env")
    got = {}
    if os.path.exists(env):
        with open(env, encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r"^(DEFAULT_ADMIN_EMAIL|DEFAULT_ADMIN_PASSWORD)=(.*)$",
                             line.strip())
                if m:
                    got[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return (got.get("DEFAULT_ADMIN_EMAIL", ""),
            got.get("DEFAULT_ADMIN_PASSWORD", ""))


def _combs():
    """Every char_cells field in the live database, grouped by template."""
    from app.db.session import SessionLocal
    from app.models.document import DocumentField, DocumentTemplate

    db = SessionLocal()
    try:
        out = {}
        rows = (db.query(DocumentField)
                  .filter(DocumentField.field_type == "char_cells").all())
        for f in rows:
            # db.get(), not the legacy Query.get() -- the latter is deprecated
            # in SQLAlchemy 2.x and emits a LegacyAPIWarning on every field,
            # which buries this script's actual measurement output.
            tpl = db.get(DocumentTemplate, f.template_id)
            if tpl is None:
                continue
            meta = f.meta or {}
            out.setdefault((tpl.id, tpl.name, tpl.data_source), []).append({
                "field_key": f.field_key,
                "page": int(f.page or 1),
                "x": float(f.x or 0), "y": float(f.y or 0),
                "width": float(f.width or 0),
                "cell_count": int(meta.get("cell_count") or 0),
                "cell_width": float(meta.get("cell_width") or 0),
                "cell_gap": float(meta.get("cell_gap") or 0),
                "group_sizes": meta.get("group_sizes"),
                "char_offset": int(meta.get("char_offset") or 0),
                "format": str(meta.get("format") or ""),
            })
        return out
    finally:
        db.close()


def _a_record(data_source: str):
    """Newest record id for the template's data source, or None."""
    from app.db.session import SessionLocal
    from app.models.candidate import Candidate
    from app.models.client import Client
    from app.models.demand import Demand

    model = {"candidate": Candidate, "demand": Demand,
             "client": Client}.get(data_source)
    if model is None:
        return None
    db = SessionLocal()
    try:
        row = db.query(model.id).order_by(model.id.desc()).first()
        return int(row[0]) if row else None
    finally:
        db.close()


def main() -> int:
    base = (sys.argv[1] if len(sys.argv) > 1
            else os.environ.get("PORTAL_URL") or "https://localhost:3000")
    base = base.rstrip("/")
    if base.startswith("http://") and "localhost" in base:
        print("WARNING: the access_token cookie is secure=True in production "
              "mode, so a plain-HTTP localhost origin drops it and every "
              "request will 401. Pass the HTTPS URL instead.", file=sys.stderr)

    email, password = _creds()
    if not email or not password:
        print("FAIL: DEFAULT_ADMIN_EMAIL / DEFAULT_ADMIN_PASSWORD not in .env")
        return 1

    combs = _combs()
    if not combs:
        print("FAIL: no char_cells fields configured at all")
        return 1

    targets = []
    for (tpl_id, name, source), fields in sorted(combs.items()):
        rid = _a_record(source)
        targets.append({"tpl_id": tpl_id, "name": name, "source": source,
                        "record_id": rid, "fields": fields})

    from app.services.pdf_engine import cell_slots

    # The browser half. Node/Playwright is driven through a small script so the
    # measurement happens in a real layout engine, not in Python.
    driver = os.path.join(REPO_ROOT, "scripts", "_comb_probe.py")
    payload = json.dumps({"base": base, "email": email, "password": password,
                          "targets": targets})
    proc = subprocess.run([sys.executable, driver], input=payload,
                          capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        print(f"FAIL: browser probe failed\n{proc.stdout}\n{proc.stderr}")
        return 1
    try:
        measured = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(f"FAIL: probe returned non-JSON:\n{proc.stdout[-2000:]}")
        return 1

    failures, empties, checked = [], [], 0
    for entry in measured:
        print(f"\n=== {entry['name']} (template {entry['tpl_id']}, "
              f"record {entry.get('record_id')}) ===")
        for f in entry["fields"]:
            key = f["field_key"]
            spec = next((s for s in
                         combs[(entry["tpl_id"], entry["name"],
                                entry["source"])]
                         if s["field_key"] == key
                         and s["char_offset"] == f["char_offset"]), None)
            if spec is None:
                continue
            chars = f["chars"]
            label = f"{key}{'(row2)' if f['char_offset'] else ''}"
            # A comb with no spans at all, or only blank ones, has no DATA --
            # which says nothing about its geometry. Reporting that as a
            # geometry failure is a false alarm (an earlier version of this
            # check did exactly that).
            if not any(c["t"].strip() for c in chars):
                empties.append(label)
                print(f"  {label:26s} DATA EMPTY -- geometry not exercised")
                continue

            count = spec["cell_count"] or len(chars)
            slots = cell_slots(count, spec["group_sizes"])
            gap = spec["cell_gap"]
            pitch = spec["cell_width"]
            if pitch <= 0:
                span = (max(slots) + 1) if slots else count
                pitch = (spec["width"] - gap * max(0, span - 1)) / max(1, span)
            tol = pitch / 4.0

            worst = 0.0
            for i, ch in enumerate(chars):
                if i >= len(slots):
                    break
                # Blank cells carry no ink, so their centre is not meaningful --
                # but they still OCCUPY cell i, which is why they are not
                # skipped when indexing.
                if not ch["t"].strip():
                    continue
                # Expected centre of cell i, in CSS px relative to the field box
                want = (slots[i] * (pitch + gap) + pitch / 2.0) * PT_TO_CSS
                worst = max(worst, abs(ch["cx"] - want))
            checked += 1
            ok = worst <= tol * PT_TO_CSS
            text = "".join(c["t"] for c in chars)
            print(f"  {label:26s} chars={len(chars):<3d} {text[:22]!r:24s} "
                  f"worst={worst / PT_TO_CSS:6.2f}pt tol={tol:5.2f} "
                  f"{'OK' if ok else 'FAIL'}")
            if not ok:
                failures.append(f"{entry['name']}/{label}: "
                                f"{worst / PT_TO_CSS:.2f}pt > {tol:.2f}pt")

    print(f"\n{'=' * 68}")
    print(f"combs measured in a real browser: {checked}")
    if empties:
        print(f"skipped (no data, geometry not exercised): {', '.join(empties)}")
    if failures:
        print("OVERALL: FAIL")
        for line in failures:
            print(f"  {line}")
        return 1
    print("OVERALL: PASS -- every rendered character is inside its printed cell")
    return 0


if __name__ == "__main__":
    sys.exit(main())
