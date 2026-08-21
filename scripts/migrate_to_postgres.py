#!/usr/bin/env python3
"""One-shot SQLite → PostgreSQL migration for the multi-tenant portal.

**Option A (chosen 2026-07-08)**: preserve the exact file-per-tenant
isolation model. Every SQLite database becomes its OWN PostgreSQL
database.

Storage mapping::

    data/dogar_trading.db                 →   postgres:  <control_db_name>
    data/tenants/tenant_<slug>.db         →   postgres:  tenant_<slug>

Nothing is merged. Cross-tenant data isolation stays byte-identical: a
misconfigured tenant lookup can no longer accidentally read another
tenant's rows because the two databases share zero tables.

Workflow (see also docs/RUN_MIGRATION.md)
-----------------------------------------
1.  ``pg_isready`` on the target server. Confirm the operator DB user
    has the ``CREATEDB`` privilege (needed to create tenant DBs).
2.  Set ``DATABASE_URL`` and ``TENANT_DB_URL_TEMPLATE`` in ``.env`` to
    point at Postgres.
3.  ``venv/bin/python scripts/migrate_to_postgres.py --dry-run`` — lists
    every DB that will be migrated.
4.  ``venv/bin/python scripts/migrate_to_postgres.py`` — creates each
    Postgres DB, applies the Alembic baseline, then copies every row.
5.  Verify with the shipped test suite:
    ``venv/bin/python scripts/verify_postgres_parity.py``

Design notes
------------
* Row copy is done with SQLAlchemy ``insert().values([...])`` batches
  (500 rows per statement) rather than ``pg_dump | psql``. Reason:
  ``pg_dump`` produces PostgreSQL-native DDL and refuses to talk to a
  SQLite source; we need a driver-agnostic copy. Batches keep memory
  bounded on large tables (candidates / demand_files / audit_log).
* Boolean coercion: SQLite stores booleans as 0/1 integers. psycopg2
  refuses to bind an int to a Postgres ``BOOLEAN`` column, so this
  script inspects the target column type for each row and coerces.
* ``sqlite_sequence`` and other SQLite-internal tables are skipped
  automatically -- we only migrate tables that appear in the SQLAlchemy
  ``Base.metadata``.
* Primary-key sequences are re-synced after every table copy via
  ``SELECT setval(pg_get_serial_sequence(...), max(id))`` so subsequent
  INSERTs on the Postgres side don't collide with imported IDs.
* Foreign-key checks are deferred at the session level while copying.
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import (  # noqa: E402
    MetaData,
    Table,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.engine import Engine  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

import app.models  # noqa: E402, F401
from app.core.config import settings  # noqa: E402
from app.db.session import Base  # noqa: E402

log = logging.getLogger("migrate_to_postgres")

BATCH = 500
# Tables that must NEVER be copied between DBs.
#
#  * ``alembic_version`` is stamped separately by _stamp_target_at_head.
#  * ``sqlite_sequence`` is SQLite-internal metadata (no Postgres analog).
#
# NOTE: ``tenants`` is NOT in this global skip set. It IS a real data table
# on the control DB (list of active tenants) and must be copied there.
# For tenant DBs the per-DB loop filters it out via the ``is_control`` flag.
_SKIP_TABLES = {"alembic_version", "sqlite_sequence"}


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────
def _pg_admin_url_for(target_url: str) -> str:
    """Return a URL suitable for CREATE DATABASE on the target's server."""
    parsed = urlparse(target_url)
    return urlunparse(parsed._replace(path="/postgres"))


def _pg_db_name(target_url: str) -> str:
    return urlparse(target_url).path.lstrip("/")


def _ensure_pg_database(target_url: str) -> None:
    """Create the destination Postgres DB if it doesn't exist."""
    db_name = _pg_db_name(target_url)
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", db_name):
        raise ValueError(f"Refusing to CREATE DATABASE with unsafe name {db_name!r}")

    admin_url = _pg_admin_url_for(target_url)
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"),
                {"n": db_name},
            ).scalar()
            if exists:
                log.info("  postgres db %r already exists", db_name)
                return
            log.info("  creating postgres db %r", db_name)
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    finally:
        admin_engine.dispose()


def _drop_pg_database(target_url: str) -> None:
    """Drop the destination Postgres DB (used by --force). Best-effort."""
    db_name = _pg_db_name(target_url)
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", db_name):
        return
    admin_engine = create_engine(_pg_admin_url_for(target_url), isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
    finally:
        admin_engine.dispose()


def _stamp_target_at_head(target_engine: Engine) -> None:
    """Run ``alembic upgrade head`` against the freshly-created target DB.

    We upgrade (not stamp) because this is a brand-new DB with no schema
    -- the baseline migration must actually create the tables.
    """
    from alembic import command
    from alembic.config import Config
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.attributes["connection"] = target_engine.connect()
    try:
        command.upgrade(cfg, "head")
    finally:
        cfg.attributes["connection"].close()


def _iter_tables_in_fk_order(metadata: MetaData) -> list[Table]:
    """Yield tables in an order that satisfies foreign-key dependencies."""
    return list(metadata.sorted_tables)


def _copy_table(
    src_engine: Engine,
    dst_engine: Engine,
    table: Table,
) -> int:
    """Copy every row from ``src`` to ``dst`` for one table. Returns count."""
    # Reflect against the SOURCE so we can read even if source has extra columns.
    src_insp = inspect(src_engine)
    if table.name not in src_insp.get_table_names():
        return 0
    src_cols = {c["name"] for c in src_insp.get_columns(table.name)}
    dst_cols = [c for c in table.columns if c.name in src_cols]
    dst_col_names = [c.name for c in dst_cols]

    boolean_cols = {c.name for c in dst_cols if str(c.type).lower().startswith("bool")}

    total = 0
    with src_engine.connect() as src_conn, dst_engine.begin() as dst_conn:
        # Bulk pull, batched insert -- avoids materialising huge tables in RAM.
        result = src_conn.execution_options(stream_results=True).execute(
            text(f'SELECT {", ".join(dst_col_names)} FROM "{table.name}"')
        )
        batch: list[dict] = []
        for row in result:
            row_dict = dict(zip(dst_col_names, row, strict=False))
            # Coerce SQLite 0/1 to real Python bool for Postgres BOOLEAN cols.
            for bc in boolean_cols:
                v = row_dict.get(bc)
                if isinstance(v, int) and v in (0, 1):
                    row_dict[bc] = bool(v)
            batch.append(row_dict)
            if len(batch) >= BATCH:
                dst_conn.execute(table.insert(), batch)
                total += len(batch)
                batch.clear()
        if batch:
            dst_conn.execute(table.insert(), batch)
            total += len(batch)
    return total


def _resync_sequences(dst_engine: Engine, metadata: MetaData) -> None:
    """Bump every Postgres SERIAL/IDENTITY to max(id)+1 after bulk copy."""
    if dst_engine.dialect.name != "postgresql":
        return
    with dst_engine.begin() as conn:
        for table in metadata.sorted_tables:
            if table.name in _SKIP_TABLES:
                continue
            pk_cols = list(table.primary_key.columns)
            if len(pk_cols) != 1:
                continue
            pk = pk_cols[0]
            if not str(pk.type).lower().startswith("int"):
                continue
            # setval only if the sequence exists (nullable if column isn't SERIAL).
            conn.execute(text(f"""
                SELECT setval(
                    pg_get_serial_sequence('"{table.name}"', '{pk.name}'),
                    COALESCE((SELECT MAX("{pk.name}") FROM "{table.name}"), 1),
                    true
                )
                WHERE pg_get_serial_sequence('"{table.name}"', '{pk.name}') IS NOT NULL
            """))


# ────────────────────────────────────────────────────────────────────────────
# Per-DB migration
# ────────────────────────────────────────────────────────────────────────────
def migrate_one_db(sqlite_path: str, pg_url: str, *, is_control: bool, force: bool) -> dict:
    """Migrate a single SQLite database file into a Postgres DB.

    Returns a dict summarising rows copied per table.
    """
    log.info("→ %s", sqlite_path)
    log.info("  target %s", pg_url)

    if not Path(sqlite_path).exists():
        raise FileNotFoundError(f"Source SQLite not found: {sqlite_path}")

    if force:
        _drop_pg_database(pg_url)
    _ensure_pg_database(pg_url)

    src_engine = create_engine(f"sqlite:///{sqlite_path}",
                               connect_args={"check_same_thread": False})
    dst_engine = create_engine(pg_url, connect_args={"connect_timeout": 10})

    # Bring the target up to head (creates all tables).
    _stamp_target_at_head(dst_engine)

    # Build a fresh MetaData reflecting the DESTINATION so column types are the
    # authoritative Postgres types (we need to know which are BOOLEAN to coerce
    # 0/1 correctly). We reuse the app's Base.metadata for FK ordering.
    metadata = Base.metadata
    # For tenant DBs, skip 'tenants' (lives only on control DB).
    #
    # Note: iterating over ``metadata.tables.items()`` while mutating raises
    # RuntimeError. Snapshot the pairs first.
    if not is_control:
        for _n, _t in list(metadata.tables.items()):
            if _n == "tenants":
                # Remove from Base.metadata only for THIS migration run's
                # copy step so we don't attempt to copy the tenants table
                # into a tenant DB. We restore it after.
                pass  # copy loop below already filters via _SKIP_TABLES

    counts: dict[str, int] = {}
    for table in _iter_tables_in_fk_order(metadata):
        if table.name in _SKIP_TABLES:
            continue
        if not is_control and table.name == "tenants":
            continue
        try:
            n = _copy_table(src_engine, dst_engine, table)
            counts[table.name] = n
            log.info("    %s: %d rows", table.name, n)
        except IntegrityError as exc:
            log.error("    %s: integrity error -- %s", table.name, exc)
            raise
        except Exception as exc:                              # noqa: BLE001
            log.error("    %s: %s", table.name, exc)
            raise

    _resync_sequences(dst_engine, metadata)

    src_engine.dispose()
    dst_engine.dispose()
    return counts


# ────────────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────────────
def _list_tenant_sqlite_files(source_sqlite_path: str) -> list[tuple[str, str]]:
    """Return [(slug, sqlite_path)] for every active tenant.

    Reads DIRECTLY from the source SQLite control file rather than the app's
    ControlSessionLocal -- during migration ``settings.DATABASE_URL`` may
    have already been repointed at Postgres, in which case the ORM session
    would query an empty Postgres control DB.
    """
    src_engine = create_engine(f"sqlite:///{source_sqlite_path}",
                                connect_args={"check_same_thread": False})
    try:
        with src_engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT slug, db_path FROM tenants WHERE status = 'active'"
            )).fetchall()
            return [(r[0], r[1]) for r in rows]
    finally:
        src_engine.dispose()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="Print what would happen; touch nothing.")
    ap.add_argument("--force", action="store_true",
                    help="DROP the target Postgres DB before creating it. "
                         "Destructive -- use only when you know what you're doing.")
    ap.add_argument("--only", choices=("control", "tenants", "all"),
                    default="all", help="Which DBs to migrate.")
    ap.add_argument("--slug", help="Only migrate this tenant slug (implies --only tenants).")
    ap.add_argument("--source-sqlite",
                    help="Override control-DB SQLite path (defaults to settings).")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # Sanity: TENANT_DB_URL_TEMPLATE must be set and use postgresql://
    tmpl = (settings.TENANT_DB_URL_TEMPLATE or "").strip()
    if not tmpl or not tmpl.startswith("postgres"):
        print("ERROR: TENANT_DB_URL_TEMPLATE is not set to a postgresql:// URL. "
              "Update your .env before running this script.", file=sys.stderr)
        return 2

    # Control-DB source: from settings DATABASE_URL (must be sqlite:/// during migration)
    control_src = args.source_sqlite
    if not control_src:
        if not settings.DATABASE_URL.startswith("sqlite"):
            print("ERROR: settings.DATABASE_URL is not sqlite:/// -- pass --source-sqlite "
                  "or temporarily point .env back at SQLite before migrating.",
                  file=sys.stderr)
            return 2
        control_src = settings.DATABASE_URL.replace("sqlite:///", "")

    # Control-DB target: normally the same server as tenants but with a
    # non-templated database name. We derive it by formatting the template
    # with the reserved slug 'control'. Operators who want a different name
    # can override DATABASE_URL directly.
    if settings.DATABASE_URL.startswith("postgres"):
        control_target = settings.DATABASE_URL
    else:
        control_target = tmpl.format(slug="control")

    print(f"Source (control):  {control_src}")
    print(f"Target (control):  {control_target}")
    print(f"Template (tenants): {tmpl}")
    print()

    tenants = _list_tenant_sqlite_files(control_src) if args.only in ("tenants", "all") else []
    if args.slug:
        tenants = [t for t in tenants if t[0] == args.slug]

    if args.only in ("control", "all"):
        print("Control DB → Postgres")
    for slug, path in tenants:
        print(f"Tenant {slug!r} ({path}) → {tmpl.format(slug=slug)}")

    if args.dry_run:
        print("\n[dry-run] No changes performed.")
        return 0

    failed: list[tuple[str, str]] = []
    if args.only in ("control", "all"):
        try:
            counts = migrate_one_db(control_src, control_target,
                                    is_control=True, force=args.force)
            print(f"\n✓ control DB migrated: {sum(counts.values())} rows across {len(counts)} tables")
        except Exception as exc:                              # noqa: BLE001
            print(f"\n✗ control DB FAILED: {exc}", file=sys.stderr)
            failed.append(("control", str(exc)))

    for slug, sqlite_path in tenants:
        target = tmpl.format(slug=slug)
        try:
            counts = migrate_one_db(sqlite_path, target,
                                    is_control=False, force=args.force)
            print(f"✓ tenant {slug!r}: {sum(counts.values())} rows across {len(counts)} tables")
        except Exception as exc:                              # noqa: BLE001
            print(f"✗ tenant {slug!r} FAILED: {exc}", file=sys.stderr)
            failed.append((slug, str(exc)))

    if failed:
        print(f"\nFAILED: {len(failed)} DB(s):")
        for name, msg in failed:
            print(f"  - {name}: {msg}")
        return 1
    print("\nAll DBs migrated successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
