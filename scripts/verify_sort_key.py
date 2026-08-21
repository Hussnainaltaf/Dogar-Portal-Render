#!/usr/bin/env python3
"""Verify (and optionally repair) the denormalised ``candidates.latest_demand_id``.

WHY THIS EXISTS
---------------
Migration 3b7d2e4f8a15 replaced a correlated scalar subquery in the candidates
list with a denormalised, indexed sort key maintained by database triggers. The
triggers were verified against all eight application mutation paths, but a
denormalised column still deserves an independent auditor, because:

  * an operator can edit the database by hand (support work, data imports);
  * databases upgraded before migration 8a4d6c2b1e90 may have historical drift
    from the former job_category re-parent edge case;
  * a restored-from-backup or externally-migrated database may predate the
    triggers entirely.

A stale value here is an ordering-corruption bug: the candidates list would
silently paginate in the wrong order rather than fail loudly. This script
recomputes the truth value with the same expression the migration uses and
reports any row that disagrees.

Checks every database: the control DB and every tenant DB.

Usage
-----
    venv/bin/python scripts/verify_sort_key.py              # audit, read-only
    venv/bin/python scripts/verify_sort_key.py --repair     # fix any drift
    venv/bin/python scripts/verify_sort_key.py --slug acme  # one tenant only

Exit codes
----------
    0    every database is consistent (or drift was repaired with --repair)
    1    drift found (audit mode), or a database could not be checked
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import create_engine, inspect, text  # noqa: E402

import app.models  # noqa: E402, F401  -- register mappers
from app.core.config import settings  # noqa: E402

# Same expression as migration 3b7d2e4f8a15 and tests/test_sort_key_integrity.py.
# If you change one, change all three.
_TRUTH = """
    SELECT MAX(d.id)
      FROM candidate_assignments ca
      JOIN job_categories jc ON jc.id = ca.job_category_id
      JOIN demands d ON d.id = jc.demand_id
     WHERE ca.candidate_id = candidates.id
"""

_DRIFT_QUERY = f"""
    SELECT id, latest_demand_id, ({_TRUTH}) AS truth
      FROM candidates
     WHERE COALESCE(latest_demand_id, -1) <> COALESCE(({_TRUTH}), -1)
     ORDER BY id
"""

_REPAIR_QUERY = f"UPDATE candidates SET latest_demand_id = ({_TRUTH})"


def _iter_databases(slug: str | None) -> list[tuple[str, str]]:
    """Return [(label, url)] for the control DB plus each tenant DB."""
    out: list[tuple[str, str]] = []
    if slug is None:
        out.append(("control", settings.DATABASE_URL))

    template = getattr(settings, "TENANT_DB_URL_TEMPLATE", "") or ""
    slugs: list[str] = []
    try:
        eng = create_engine(settings.DATABASE_URL)
        with eng.connect() as conn:
            if "tenants" in inspect(eng).get_table_names():
                rows = conn.execute(text("SELECT slug FROM tenants")).fetchall()
                slugs = [r[0] for r in rows]
    except Exception as exc:  # pragma: no cover - control DB unreachable
        print(f"  ! could not enumerate tenants: {exc}")

    for s in slugs:
        if slug is not None and s != slug:
            continue
        if template:
            out.append((f"tenant:{s}", template.format(slug=s)))
        else:
            path = PROJECT_ROOT / "data" / "tenants" / f"tenant_{s}.db"
            if path.exists():
                out.append((f"tenant:{s}", f"sqlite:///{path}"))
    return out


def _check_one(label: str, url: str, repair: bool) -> tuple[bool, int]:
    """Return (ok, drift_count) for a single database."""
    try:
        eng = create_engine(url)
        with eng.connect() as conn:
            tables = set(inspect(eng).get_table_names())
            if "candidates" not in tables:
                print(f"  {label:<22} SKIP (no candidates table)")
                return True, 0
            cols = {c["name"] for c in inspect(eng).get_columns("candidates")}
            if "latest_demand_id" not in cols:
                print(f"  {label:<22} SKIP (migration 3b7d2e4f8a15 not applied)")
                return True, 0

            drift = conn.execute(text(_DRIFT_QUERY)).fetchall()
            total = conn.execute(text("SELECT COUNT(*) FROM candidates")).scalar()

            if not drift:
                print(f"  {label:<22} OK   ({total} candidates, 0 drift)")
                return True, 0

            print(f"  {label:<22} DRIFT ({len(drift)} of {total} rows)")
            for row in drift[:10]:
                print(f"      candidate {row[0]}: stored={row[1]!r} truth={row[2]!r}")
            if len(drift) > 10:
                print(f"      ... and {len(drift) - 10} more")

            if repair:
                conn.execute(text(_REPAIR_QUERY))
                conn.commit()
                left = conn.execute(text(_DRIFT_QUERY)).fetchall()
                if left:
                    print(f"      REPAIR FAILED, {len(left)} rows still drifting")
                    return False, len(drift)
                print(f"      repaired {len(drift)} rows")
                return True, len(drift)
            return False, len(drift)
    except Exception as exc:
        print(f"  {label:<22} ERROR {exc}")
        return False, 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repair", action="store_true",
                    help="recompute and write the correct value for drifting rows")
    ap.add_argument("--slug", help="check only this tenant")
    args = ap.parse_args()

    dbs = _iter_databases(args.slug)
    if not dbs:
        print("No databases to check.")
        return 0

    mode = "REPAIR" if args.repair else "AUDIT"
    print(f"candidates.latest_demand_id integrity [{mode}] -- {len(dbs)} database(s)\n")

    all_ok = True
    total_drift = 0
    for label, url in dbs:
        ok, n = _check_one(label, url, args.repair)
        all_ok &= ok
        total_drift += n

    print()
    if total_drift == 0 and all_ok:
        print("RESULT: consistent -- every candidates.latest_demand_id matches truth.")
        return 0
    if args.repair and all_ok:
        print(f"RESULT: repaired {total_drift} drifting row(s).")
        return 0
    print(f"RESULT: {total_drift} drifting row(s) found. Re-run with --repair to fix.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
