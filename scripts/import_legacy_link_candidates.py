"""
Phase-3 legacy migration — LINK candidates to their demand/trade.

Background
----------
The business flow is strictly:

    Client  ->  Demand (visa_receipts / "demand file")
            ->  JobCategory (visa_receipts_items / a trade line in the demand)
            ->  CandidateAssignment  ->  Candidate

A candidate is ONLY ever attached to the system through an assignment to a
trade (job_category) inside a demand. There is no other way to add/assign a
candidate. Phase-1 imported the candidates and phase-2 imported the demands +
job_categories, but NEITHER created the linking ``candidate_assignments`` rows,
so every candidate showed "—" for CLIENT and DEMAND FILE in the UI.

This script reconstructs those links from the legacy dump:

    passport_recevings.vid  -> visa_receipts.id        (the demand)
    passport_recevings.rid  -> visa_receipts_items.id  (the trade line)

Legacy ids were preserved in the ``notes`` of the imported rows:
    candidate.notes : "Imported from legacy backup (legacy id <pr_id>)."
    demand.notes    : "Imported from legacy visa_receipts (id <vid>)."

We map:
    pr_id  -> portal candidate.id      (from candidate.notes)
    vid    -> portal demand.id         (from demand.notes)
    rid    -> visa_receipts_items row  -> (visa_receipt_id, category_id)
              -> the demand's job_category whose trade == that category name
                 (fallback: the demand's first job_category)

Idempotent: skips a candidate that already has an assignment, and never
creates a duplicate (candidate_id, job_category_id) pair.

Run:
    TESTING=true venv/bin/python scripts/import_legacy_link_candidates.py \
        /home/user/uploaded_files/backup.sql
"""
import csv
import io
import re
import sys

sys.path.insert(0, ".")
from app.db.session import SessionLocal  # noqa: E402
from app.models import Demand, JobCategory  # noqa: E402
from app.models.candidate import Candidate, CandidateAssignment  # noqa: E402

INSERT_RE = re.compile(r"^INSERT INTO (\w+) VALUES\((.*)\);\s*$")

# Legacy-id extractors from the notes we wrote during phase 1/2.
CAND_LEGACY_RE = re.compile(r"legacy id (\d+)")
DEMAND_LEGACY_RE = re.compile(r"visa_receipts \(id (\d+)\)")


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


def norm(s):
    return (s or "").strip().lower()


def main(dump_path):
    tables = parse_dump(dump_path)
    pr_rows = tables.get("passport_recevings", [])
    vri_rows = tables.get("visa_receipts_items", [])
    vc_rows = tables.get("visa_categories", [])
    print(f"legacy passport_recevings: {len(pr_rows)}")
    print(f"legacy visa_receipts_items: {len(vri_rows)}")

    # legacy visa_category id -> name (0=id, 1=name)
    vc_name = {g(r, 0): (g(r, 1) or "").strip() for r in vc_rows}

    # legacy visa_receipts_items.id -> (visa_receipt_id, category_id)
    # schema: 0 id, 1 visa_receipt_id, 2 category_id, ...
    vri_by_id = {}
    for r in vri_rows:
        vri_by_id[g(r, 0)] = (g(r, 1), g(r, 2))

    db = SessionLocal()
    try:
        # Build portal maps from the preserved legacy-id notes.
        cand_by_legacy = {}   # pr_id -> Candidate
        for c in db.query(Candidate).all():
            m = CAND_LEGACY_RE.search(c.notes or "")
            if m:
                cand_by_legacy[m.group(1)] = c

        demand_by_legacy = {}  # vid -> Demand
        for d in db.query(Demand).all():
            m = DEMAND_LEGACY_RE.search(d.notes or "")
            if m:
                demand_by_legacy[m.group(1)] = d

        # demand.id -> list[JobCategory]
        jobcats_by_demand = {}
        for j in db.query(JobCategory).all():
            jobcats_by_demand.setdefault(j.demand_id, []).append(j)

        # existing assignments to keep idempotent
        existing_pairs = {
            (a.candidate_id, a.job_category_id)
            for a in db.query(CandidateAssignment).all()
        }
        already_assigned_candidates = {
            a.candidate_id for a in db.query(CandidateAssignment).all()
        }

        print(f"portal candidates w/ legacy id: {len(cand_by_legacy)}")
        print(f"portal demands w/ legacy id: {len(demand_by_legacy)}")

        stats = {
            "linked": 0,
            "skip_already": 0,
            "no_candidate": 0,
            "no_demand": 0,
            "no_jobcat": 0,
            "trade_matched": 0,
            "trade_fallback": 0,
        }

        n = 0
        for r in pr_rows:
            pr_id = g(r, 0)
            vid = g(r, 1)
            rid = g(r, 2)

            cand = cand_by_legacy.get(pr_id)
            if cand is None:
                stats["no_candidate"] += 1
                continue
            if cand.id in already_assigned_candidates:
                stats["skip_already"] += 1
                continue

            dem = demand_by_legacy.get(vid)
            if dem is None:
                stats["no_demand"] += 1
                continue

            jobcats = jobcats_by_demand.get(dem.id, [])
            if not jobcats:
                stats["no_jobcat"] += 1
                continue

            # Choose the trade line. Use rid -> visa_receipts_items -> category
            # name, and match a job_category under this demand by trade name.
            chosen = None
            vri = vri_by_id.get(rid)
            if vri:
                _, cat_id = vri
                cat_name = norm(vc_name.get(cat_id, ""))
                if cat_name:
                    for jc in jobcats:
                        if norm(jc.trade) == cat_name:
                            chosen = jc
                            break
            if chosen is not None:
                stats["trade_matched"] += 1
            else:
                # Fallback: attach to the demand's first trade line so the
                # Client -> Demand -> Candidate chain is still correct.
                chosen = jobcats[0]
                stats["trade_fallback"] += 1

            pair = (cand.id, chosen.id)
            if pair in existing_pairs:
                stats["skip_already"] += 1
                continue

            # Map legacy status to an assignment status.
            leg_status = norm(getattr(cand, "status", "") or "")
            assign_status = "processing"
            if leg_status in ("deployed", "issued", "visa_issued"):
                assign_status = leg_status if leg_status != "issued" else "visa_issued"

            # Persist the embassy on the assignment too. The embassy belongs to
            # the demand (visa_receipts.emb_id) and determines which visa form
            # (Islamabad vs Karachi) is available for THIS candidate on THIS
            # demand file. Mirror the demand's embassy onto the assignment so
            # the per-assignment field is the single source of truth for the
            # candidate's visa-form availability.
            db.add(CandidateAssignment(
                candidate_id=cand.id,
                job_category_id=chosen.id,
                status=assign_status,
                embassy=(dem.embassy or ""),
                notes=f"Linked from legacy passport_recevings (id {pr_id}, "
                      f"vid {vid}, rid {rid}).",
            ))
            existing_pairs.add(pair)
            already_assigned_candidates.add(cand.id)
            stats["linked"] += 1
            n += 1
            if n % 400 == 0:
                db.commit()
                print(f"  committed {n} assignments…")
        db.commit()

        print("\n=== LINKAGE SUMMARY ===")
        for k, v in stats.items():
            print(f"  {k:20s}: {v}")
    finally:
        db.close()


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "/home/user/uploaded_files/backup.sql"
    main(path)
