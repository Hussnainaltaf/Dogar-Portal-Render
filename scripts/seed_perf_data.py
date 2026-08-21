#!/usr/bin/env python3
"""
Seed a realistic-volume dataset for PERFORMANCE testing.

Why this exists
---------------
The 2026-07-09 benchmark in docs/PERFORMANCE_REPORT.md was run against an
EMPTY database (0 clients / 0 candidates). Latency on an empty table says
nothing about production behaviour: every query returns immediately, no index
is ever exercised, and N+1 query patterns are invisible because there are no
rows to iterate. A "56 req/s, p95 455ms" number from an empty DB is noise.

This script creates a dataset sized like a busy OEP promoter's few years of
operation, so the benchmark measures real query cost and surfaces N+1 patterns.

SAFETY
------
  * Refuses to run unless --i-understand-this-writes-data is passed.
  * Refuses to run against a DB that already holds non-seed rows unless
    --allow-existing is given, so it can never quietly pollute real records.
  * Every row it creates is tagged with a recognisable marker
    (PERFSEED) so it can be identified and removed with --purge.

Usage
-----
    ./venv/bin/python scripts/seed_perf_data.py --clients 120 --demands 400 \
        --candidates 5000 --i-understand-this-writes-data
    ./venv/bin/python scripts/seed_perf_data.py --purge
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, ".")

from app.core.workflow import CandidateStage  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.candidate import Candidate, CandidateAssignment  # noqa: E402
from app.models.client import Client  # noqa: E402
from app.models.demand import Demand, JobCategory  # noqa: E402

MARKER = "PERFSEED"

TRADES = [
    "Mason", "Steel Fixer", "Shuttering Carpenter", "Electrician", "Plumber",
    "Heavy Driver", "Light Driver", "Welder", "AC Technician", "Labour",
    "Cook", "Cleaner", "Security Guard", "Salesman", "Store Keeper",
]
COUNTRIES = ["Saudi Arabia", "UAE", "Qatar", "Oman", "Kuwait", "Bahrain", "Malaysia"]
CITIES = ["Lahore", "Karachi", "Faisalabad", "Gujranwala", "Multan", "Rawalpindi"]


def _rand_dt(days_back: int) -> datetime:
    return datetime.now(UTC) - timedelta(
        days=random.randint(0, days_back), hours=random.randint(0, 23)
    )


def purge(db) -> None:
    """Remove every row this script created (identified by the marker).

    Order matters: children before parents. Job categories and assignments carry
    no marker of their own (they have no free-text name column), so they are
    identified by reachability from a marked demand / candidate. Deleting the
    parents first would orphan them and leave the DB dirtier than before the
    purge -- which is worse than not purging at all, because the leftovers are
    then unfindable.
    """
    marked_demand_ids = [
        row.id
        for row in db.query(Demand.id).filter(Demand.file_number.like(f"%{MARKER}%"))
    ]
    marked_cand_ids = [
        row.id
        for row in db.query(Candidate.id).filter(Candidate.full_name.like(f"%{MARKER}%"))
    ]
    marked_cat_ids = [
        row.id
        for row in db.query(JobCategory.id).filter(JobCategory.demand_id.in_(marked_demand_ids))
    ] if marked_demand_ids else []

    a = 0
    if marked_cand_ids or marked_cat_ids:
        # SQLite caps a statement at ~999 bind parameters, so chunk the IN lists.
        def _chunks(seq, size=800):
            for i in range(0, len(seq), size):
                yield seq[i : i + size]

        for chunk in _chunks(marked_cand_ids):
            a += (
                db.query(CandidateAssignment)
                .filter(CandidateAssignment.candidate_id.in_(chunk))
                .delete(synchronize_session=False)
            )
        for chunk in _chunks(marked_cat_ids):
            a += (
                db.query(CandidateAssignment)
                .filter(CandidateAssignment.job_category_id.in_(chunk))
                .delete(synchronize_session=False)
            )
        cat = 0
        for chunk in _chunks(marked_cat_ids):
            cat += (
                db.query(JobCategory)
                .filter(JobCategory.id.in_(chunk))
                .delete(synchronize_session=False)
            )
    else:
        cat = 0

    c = db.query(Candidate).filter(Candidate.full_name.like(f"%{MARKER}%")).delete(
        synchronize_session=False
    )
    d = db.query(Demand).filter(Demand.file_number.like(f"%{MARKER}%")).delete(
        synchronize_session=False
    )
    cl = db.query(Client).filter(Client.company_name.like(f"%{MARKER}%")).delete(
        synchronize_session=False
    )
    db.commit()
    print(
        f"purged: {cl} clients, {d} demands, {cat} job categories, "
        f"{c} candidates, {a} assignments"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clients", type=int, default=120)
    ap.add_argument("--demands", type=int, default=400)
    ap.add_argument("--candidates", type=int, default=5000)
    ap.add_argument("--purge", action="store_true")
    ap.add_argument("--allow-existing", action="store_true")
    ap.add_argument("--i-understand-this-writes-data", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        if args.purge:
            purge(db)
            return 0

        if not args.i_understand_this_writes_data:
            print(
                "Refusing to run: pass --i-understand-this-writes-data.\n"
                "This script WRITES bulk rows and is for perf testing only.",
                file=sys.stderr,
            )
            return 2

        real_clients = (
            db.query(Client).filter(~Client.company_name.like(f"%{MARKER}%")).count()
        )
        real_cands = (
            db.query(Candidate).filter(~Candidate.full_name.like(f"%{MARKER}%")).count()
        )
        if (real_clients or real_cands) and not args.allow_existing:
            print(
                f"Refusing to run: DB already holds non-seed data "
                f"({real_clients} clients, {real_cands} candidates). "
                f"Use --allow-existing to override.",
                file=sys.stderr,
            )
            return 2

        random.seed(20260728)

        print(f"seeding {args.clients} clients ...")
        clients = [
            Client(
                company_name=f"{MARKER} {random.choice(['Al','Gulf','Emirates','Najd'])} "
                f"{random.choice(['Contracting','Trading','Manpower'])} {i}",
                country=random.choice(COUNTRIES),
                city=random.choice(["Riyadh", "Dubai", "Doha", "Muscat"]),
                email=f"contact{i}@example.invalid",
                phone=f"+9665{random.randint(10000000, 99999999)}",
                created_at=_rand_dt(900),
            )
            for i in range(args.clients)
        ]
        db.bulk_save_objects(clients)
        db.commit()
        client_ids = [c.id for c in db.query(Client.id).all()]

        print(f"seeding {args.demands} demands ...")
        demands = [
            Demand(
                file_number=f"{MARKER}-DF-{2024 + (i % 3)}-{i:05d}",
                client_id=random.choice(client_ids),
                country=random.choice(COUNTRIES),
                # NOTE: the column is `visa_quota`. This line read `total_quota`
                # until 2026-07-30, which made the whole script die with
                # TypeError on its first Demand -- i.e. it had never once run to
                # completion, and the "regenerate the benchmark against real
                # volume" task it exists for was silently impossible.
                # tests/test_seed_scripts.py now checks every kwarg here against
                # the mapped columns so a rename can't break it again.
                visa_quota=random.randint(5, 250),
                created_at=_rand_dt(800),
            )
            for i in range(args.demands)
        ]
        db.bulk_save_objects(demands)
        db.commit()
        demand_ids = [d.id for d in db.query(Demand.id).all()]

        # Job categories: the real domain chain is
        #   CLIENT -> DEMAND -> JOB CATEGORY -> CANDIDATE ASSIGNMENT -> CANDIDATE
        # Seeding candidates without categories/assignments leaves that join
        # graph empty, so the list endpoints never touch it and any N+1 pattern
        # stays invisible -- exactly the blind spot this script exists to remove.
        print(f"seeding job categories for {len(demand_ids)} demands ...")
        categories = []
        for did in demand_ids:
            for _ in range(random.randint(1, 4)):
                categories.append(
                    JobCategory(
                        demand_id=did,
                        trade=random.choice(TRADES),
                        quantity=random.randint(2, 60),
                        salary=float(random.randint(1000, 3500)),
                    )
                )
        db.bulk_save_objects(categories)
        db.commit()
        category_ids = [c.id for c in db.query(JobCategory.id).all()]
        print(f"  {len(category_ids)} job categories")

        stages = [s.value for s in CandidateStage]
        print(f"seeding {args.candidates} candidates ...")
        batch = []
        for i in range(args.candidates):
            batch.append(
                Candidate(
                    full_name=f"{MARKER} Worker {i:06d}",
                    father_name=f"Father {i:06d}",
                    passport_no=f"XY{random.randint(1000000, 9999999)}",
                    cnic=f"3520{random.randint(100000000, 999999999)}",
                    phone=f"+923{random.randint(100000000, 999999999)}",
                    # `city` and `trade` are NOT Candidate columns -- the home
                    # district is `district` and the occupation is `profession`
                    # (a candidate's *trade* lives on the JobCategory they are
                    # assigned to). Both were wrong here for the same reason
                    # visa_quota was: nothing executed this file.
                    district=random.choice(CITIES),
                    profession=random.choice(TRADES),
                    status=random.choice(stages),
                    created_at=_rand_dt(700),
                )
            )
            if len(batch) >= 1000:
                db.bulk_save_objects(batch)
                db.commit()
                batch.clear()
                print(f"  ... {i + 1}")
        if batch:
            db.bulk_save_objects(batch)
            db.commit()

        # Assignments: attach ~85% of candidates to a job category so the
        # candidate/demand list joins are exercised at realistic fan-out.
        print("seeding candidate assignments ...")
        cand_ids = [c.id for c in db.query(Candidate.id).all()]
        assignments = []
        for cid in cand_ids:
            if random.random() > 0.85 or not category_ids:
                continue  # a real DB always has some unassigned intake
            assignments.append(
                CandidateAssignment(
                    candidate_id=cid,
                    job_category_id=random.choice(category_ids),
                    status=random.choice(stages),
                    embassy=random.choice(["Islamabad", "Karachi"]),
                )
            )
            if len(assignments) >= 2000:
                db.bulk_save_objects(assignments)
                db.commit()
                assignments.clear()
        if assignments:
            db.bulk_save_objects(assignments)
            db.commit()

        print("\n=== volume now ===")
        print("clients    :", db.query(Client).count())
        print("demands    :", db.query(Demand).count())
        print("categories :", db.query(JobCategory).count())
        print("candidates :", db.query(Candidate).count())
        print("assignments:", db.query(CandidateAssignment).count())
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
