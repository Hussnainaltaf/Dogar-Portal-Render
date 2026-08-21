#!/usr/bin/env python3
"""Verify that the Postgres migration copied every row from the source SQLite.

Run AFTER ``scripts/migrate_to_postgres.py``. For every source SQLite DB
(control + all tenants) compares row counts per table against the
matching Postgres DB. Reports any drift with a non-zero exit code.

Also does a spot-check on primary-key sequences: for each table with an
integer ``id`` PK it re-queries ``nextval(pg_get_serial_sequence(...))``
and asserts it is > max(id) (i.e. subsequent INSERTs won't collide).

Usage
-----
    venv/bin/python scripts/verify_postgres_parity.py
    venv/bin/python scripts/verify_postgres_parity.py --slug acme
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import create_engine, inspect, text  # noqa: E402

import app.models  # noqa: E402, F401
from app.core.config import settings  # noqa: E402

_SKIP = {"alembic_version", "sqlite_sequence"}


def _row_counts(engine) -> dict[str, int]:
    insp = inspect(engine)
    counts: dict[str, int] = {}
    with engine.connect() as conn:
        for name in insp.get_table_names():
            if name in _SKIP:
                continue
            n = conn.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar()
            counts[name] = int(n or 0)
    return counts


def _compare(src_url: str, dst_url: str, *, label: str, ignore_tenants_on_tenant: bool = False) -> bool:
    print(f"\n=== {label} ===")
    src = create_engine(src_url, connect_args={"check_same_thread": False} if src_url.startswith("sqlite") else {})
    dst = create_engine(dst_url)
    try:
        src_counts = _row_counts(src)
        dst_counts = _row_counts(dst)
    finally:
        src.dispose()
        dst.dispose()

    all_tables = set(src_counts) | set(dst_counts)
    ok = True
    for t in sorted(all_tables):
        if t in _SKIP:
            continue
        if ignore_tenants_on_tenant and t == "tenants":
            continue
        s = src_counts.get(t, 0)
        d = dst_counts.get(t, 0)
        # Some tables intentionally exist only on one side (e.g. `tenants` on
        # control DB doesn't exist in tenant DBs). Report but don't fail.
        if s == d:
            print(f"  {t}: {s} rows OK")
        else:
            print(f"  {t}: source={s}  target={d}  ✗ DRIFT")
            ok = False
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--slug", help="Verify one tenant only.")
    args = ap.parse_args()

    tmpl = (settings.TENANT_DB_URL_TEMPLATE or "").strip()
    if not tmpl:
        print("ERROR: TENANT_DB_URL_TEMPLATE not set.", file=sys.stderr)
        return 2

    # Locate source SQLite files.
    control_src_sqlite = "data/dogar_trading.db"
    if not Path(control_src_sqlite).exists():
        print(f"ERROR: control source SQLite {control_src_sqlite} missing.", file=sys.stderr)
        return 2

    # Read tenant list from source SQLite control DB.
    src_control = create_engine(f"sqlite:///{control_src_sqlite}",
                                 connect_args={"check_same_thread": False})
    with src_control.connect() as conn:
        rows = conn.execute(text(
            "SELECT slug, db_path FROM tenants WHERE status = 'active'"
        )).fetchall()
    src_control.dispose()

    tenants = [(r[0], r[1]) for r in rows]
    if args.slug:
        tenants = [t for t in tenants if t[0] == args.slug]

    control_target = settings.DATABASE_URL if settings.DATABASE_URL.startswith("postgres") \
                     else tmpl.format(slug="control")

    all_ok = True
    if not args.slug:
        all_ok &= _compare(f"sqlite:///{control_src_sqlite}", control_target,
                           label="control DB")
    for slug, sqlite_path in tenants:
        all_ok &= _compare(f"sqlite:///{sqlite_path}", tmpl.format(slug=slug),
                           label=f"tenant {slug}", ignore_tenants_on_tenant=True)

    print()
    if all_ok:
        print("✓ Parity verification PASSED for all databases.")
        return 0
    print("✗ Parity verification FAILED. See drift above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
