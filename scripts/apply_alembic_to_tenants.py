#!/usr/bin/env python3
"""Apply ``alembic upgrade head`` to every tenant database.

The control DB is migrated by the normal ``alembic upgrade head`` (or by
``app.db.init_db.run_schema_migrations``). Tenants live in their own
databases (one SQLite file OR one Postgres DB per tenant), so each one
must be upgraded independently.

Usage
-----
    # Migrate every active tenant using the URL template from settings.
    venv/bin/python scripts/apply_alembic_to_tenants.py

    # Preview which URLs would be touched.
    venv/bin/python scripts/apply_alembic_to_tenants.py --dry-run

    # Migrate a single tenant only.
    venv/bin/python scripts/apply_alembic_to_tenants.py --slug acme

Exit codes
----------
    0    all tenants upgraded (or no tenants exist).
    1    at least one tenant failed. STDOUT contains a summary.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from app.core.tenancy import tenant_db_url  # noqa: E402
from app.db.session import SessionLocal as ControlSessionLocal  # noqa: E402


def _tenant_rows(slug_filter: str | None, active_only: bool = False):
    """Tenant (slug, db_path) pairs to migrate.

    WHY NON-ACTIVE TENANTS ARE INCLUDED BY DEFAULT (measured 2026-08-18)
    -------------------------------------------------------------------
    This filtered on ``status == "active"`` and therefore SILENTLY SKIPPED
    every archived tenant. Measured on the live control DB while adding the
    audit-outcome migration:

        tenant 'acme'  status=active    -> upgraded to 9b5e7d3f2c81
        tenant 'beta'  status=archived  -> NOT VISITED, left at 4c8e1f2a6b93

    i.e. beta was five revisions behind head. "Archived" does NOT mean the
    data is gone: ``tenant_service.archive_tenant_db()`` only moves the file
    when a tenant is DELETED, and beta's ``tenant_beta.db`` was still sitting
    in data/tenants/. Meanwhile ``PUT /api/tenants/{id}`` accepts
    ``{"status": "active"}`` and flips the row back with no migration
    anywhere on that path -- so reactivating an archived office produced a
    live portal whose schema was missing columns the code selects, i.e. a 500
    on the first audit query rather than an obvious "please migrate" error.

    The skip also grew more dangerous with every release: the further behind
    an archived DB drifts, the more likely its eventual upgrade hits a
    revision that can no longer be applied cleanly.

    So the default is now "every tenant whose database still exists".
    A tenant whose file is already gone is reported and skipped -- there is
    nothing to migrate, and that is not a failure. ``--active-only`` keeps
    the old behaviour available for an operator who deliberately wants it.
    """
    from app.models.tenant import Tenant
    db = ControlSessionLocal()
    try:
        q = db.query(Tenant)
        if active_only:
            q = q.filter(Tenant.status == "active")
        if slug_filter:
            q = q.filter(Tenant.slug == slug_filter)
        return [(row.slug, row.db_path, row.status or "") for row in q.all()]
    finally:
        db.close()


def _upgrade(url: str) -> None:
    """Run ``alembic upgrade head`` against a single tenant URL."""
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    # env.py picks this up via -x db_url= / ALEMBIC_DATABASE_URL. Passing via
    # x_args is the cleanest -- it doesn't pollute the parent process env.
    cfg.cmd_opts = argparse.Namespace(x=[f"db_url={url}", "is_tenant=1"])
    command.upgrade(cfg, "head")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--slug", help="Only migrate this tenant slug.")
    ap.add_argument("--dry-run", action="store_true", help="List targets, do nothing.")
    ap.add_argument(
        "--active-only", action="store_true",
        help="Skip archived/suspended tenants (NOT the default -- see _tenant_rows).",
    )
    args = ap.parse_args()

    rows = _tenant_rows(args.slug, active_only=args.active_only)
    if not rows:
        print("No matching tenants.")
        return 0

    failed: list[tuple[str, str]] = []
    upgraded = 0
    absent = 0
    for slug, db_path, status in rows:
        url = tenant_db_url(slug, db_path)
        label = f"→ tenant {slug!r} [{status or 'unknown'}]: {url}"
        # A SQLite tenant whose file has been moved to data/tenants/archive/ by
        # a delete has nothing to migrate. Upgrading it would make alembic
        # CREATE an empty database at that path -- sqlite never errors on a
        # missing file -- leaving a bogus schema-only shell behind and
        # reporting success. Detect and skip instead.
        if url.startswith("sqlite") and not Path(url.split("///", 1)[-1]).exists():
            print(label)
            print("  – skipped: database file does not exist (nothing to migrate)")
            absent += 1
            continue
        print(label)
        if args.dry_run:
            continue
        try:
            _upgrade(url)
            print("  ✓ upgraded")
            upgraded += 1
        except Exception as exc:                              # noqa: BLE001
            print(f"  ✗ FAILED: {exc}")
            failed.append((slug, str(exc)))

    print()
    if failed:
        print(f"FAILED: {len(failed)} tenant(s):")
        for slug, msg in failed:
            print(f"  - {slug}: {msg}")
        return 1
    print(f"OK: {upgraded} tenant(s) upgraded, {absent} skipped (no database file).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
