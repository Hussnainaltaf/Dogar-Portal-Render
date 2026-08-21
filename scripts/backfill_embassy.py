"""
Backfill migration — populate the EMBASSY (Islamabad / Karachi / …) on already
imported demands and candidate assignments.

Why this exists
---------------
Phase-2 imported ``visa_receipts -> demands`` but never mapped the legacy
``visa_receipts.emb_id`` (column 20) through the ``embassies`` table onto
``Demand.embassy``. Phase-3 then created ``candidate_assignments`` with an empty
``embassy``. Result: the candidate VISA PROCESS panel showed "No embassy" and no
Islamabad/Karachi visa form was available.

This script fixes the *existing* database in place (no wipe, no re-import):

    legacy embassies:  id -> name          (3="Islamabad Embassy", 4="Karachi Embassy", …)
    legacy visa_receipts:  id (=vid) -> emb_id
    portal demand.notes: "Imported from legacy visa_receipts (id <vid>)."

    => demand.embassy      = embassies[ visa_receipts[vid].emb_id ].name
    => assignment.embassy  = its demand's embassy (mirrored)

Idempotent: only writes when the value would change; safe to run repeatedly.

Run:
    TESTING=true venv/bin/python scripts/backfill_embassy.py \
        /home/user/uploaded_files/backup.sql
"""
import csv
import io
import re
import sys

sys.path.insert(0, ".")
from app.db.session import SessionLocal  # noqa: E402
from app.models import Demand, JobCategory  # noqa: E402
from app.models.candidate import CandidateAssignment  # noqa: E402

INSERT_RE = re.compile(r"^INSERT INTO (\w+) VALUES\((.*)\);\s*$")
DEMAND_LEGACY_RE = re.compile(r"visa_receipts \(id (\d+)\)")

# visa_receipts column index of emb_id (0-based). Verified against the dump
# schema: 0 id,1 file_number,2 date,3 visa_issue_date_h,4 party_id,
# 5 sponsor_name … 19 visa_country_id, 20 emb_id, …
VR_EMB_ID_COL = 20


def parse_dump(path):
    tables = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = INSERT_RE.match(line.rstrip("\n"))
            if not m:
                continue
            table, values = m.group(1), m.group(2)
            values = values.replace('\\"', '""')
            reader = csv.reader(io.StringIO(values), quotechar='"',
                                doublequote=True, skipinitialspace=True)
            row = next(reader)
            tables.setdefault(table, []).append(row)
    return tables


def g(row, i, default=""):
    return row[i] if i < len(row) else default


def main(dump_path):
    tables = parse_dump(dump_path)

    # legacy embassy id -> display name
    emb_name = {g(r, 0): (g(r, 1) or "").strip() for r in tables.get("embassies", [])}
    print(f"legacy embassies: {emb_name}")

    # legacy visa_receipts id (vid) -> emb_id
    vid_to_embid = {}
    for r in tables.get("visa_receipts", []):
        vid_to_embid[g(r, 0)] = g(r, VR_EMB_ID_COL)

    db = SessionLocal()
    try:
        demands = db.query(Demand).all()

        # portal demand.id -> resolved embassy name
        demand_embassy = {}
        stats = {
            "demands_total": len(demands),
            "demands_set": 0,
            "demands_no_legacy_note": 0,
            "demands_no_embid": 0,
            "assignments_total": 0,
            "assignments_set": 0,
        }

        for d in demands:
            m = DEMAND_LEGACY_RE.search(d.notes or "")
            if not m:
                stats["demands_no_legacy_note"] += 1
                continue
            vid = m.group(1)
            emb_id = vid_to_embid.get(vid, "")
            name = emb_name.get(emb_id, "")
            if not name:
                stats["demands_no_embid"] += 1
                demand_embassy[d.id] = d.embassy or ""
                continue
            demand_embassy[d.id] = name
            if (d.embassy or "") != name:
                d.embassy = name
                stats["demands_set"] += 1

        db.flush()

        # Mirror onto assignments via job_category -> demand.
        # Build job_category.id -> demand.id
        jc_to_demand = {jc.id: jc.demand_id for jc in db.query(JobCategory).all()}

        assignments = db.query(CandidateAssignment).all()
        stats["assignments_total"] = len(assignments)
        for a in assignments:
            dem_id = jc_to_demand.get(a.job_category_id)
            if dem_id is None:
                continue
            name = demand_embassy.get(dem_id, "")
            if name and (a.embassy or "") != name:
                a.embassy = name
                stats["assignments_set"] += 1

        db.commit()

        print("\n=== EMBASSY BACKFILL SUMMARY ===")
        for k, v in stats.items():
            print(f"  {k:26s}: {v}")

        # Distribution report
        from collections import Counter
        dist = Counter(d.embassy or "(empty)" for d in db.query(Demand).all())
        print("\n  demand embassy distribution:")
        for k, v in dist.most_common():
            print(f"    {k:28s}: {v}")
        adist = Counter(a.embassy or "(empty)"
                        for a in db.query(CandidateAssignment).all())
        print("\n  assignment embassy distribution:")
        for k, v in adist.most_common():
            print(f"    {k:28s}: {v}")
    finally:
        db.close()


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "/home/user/uploaded_files/backup.sql"
    main(path)
