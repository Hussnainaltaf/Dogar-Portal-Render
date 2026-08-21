#!/usr/bin/env python3
"""
Apply the current SQLAlchemy schema to every per-tenant SQLite database.

Background
──────────
Each "Copy Portal" business partner gets an isolated SQLite file at
``data/tenants/tenant_<slug>.db`` (see ``app/services/tenant_service.py``).
The main control DB (``data/dogar_trading.db``) is migrated with Alembic
(``alembic upgrade head``), but the per-tenant DBs are provisioned with
``Base.metadata.create_all()`` and are therefore NOT touched by Alembic.

When we ship a schema change (a new table or a new column on an existing
table), every tenant DB must be brought up to date or the app will 500 on
those tenants. This script does that automatically and idempotently:

  1. NEW TABLES     -> created via ``Base.metadata.create_all()`` (no-op if
                       the table already exists).
  2. NEW COLUMNS    -> detected by diffing each model's columns against the
                       live ``PRAGMA table_info`` and added with
                       ``ALTER TABLE ... ADD COLUMN``.
  3. NEW INDEXES    -> detected by diffing each model's named indexes against
                       the live index list and created with ``CREATE INDEX``.
                       Added 2026-08-19 after measuring that steps 1-2 alone
                       left this script and ``alembic upgrade head`` producing
                       two different schemas (see upgrade_tenant_db).

It never drops or rewrites existing tables/columns, so it is safe to run on
every deploy. SQLite's ``ALTER TABLE ADD COLUMN`` only supports adding
columns (not dropping/renaming); any destructive change still needs a manual
migration (documented in DEPLOY.md § 10).

Usage
─────
    cd dogar_trading_portal
    source venv/bin/activate
    python scripts/apply_tenant_migrations.py

Exit code is 0 on success, 1 if any tenant DB failed (the run continues past
a failing tenant so one bad file does not block the rest).
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# Make the app importable when run as a bare script (python scripts/...).
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import create_engine, event, inspect, text  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402

import app.models  # noqa: F401,E402  -- registers every model on Base.metadata
from app.db.session import Base  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("apply_tenant_migrations")

TENANTS_DIR = REPO_ROOT / "data" / "tenants"

# The Tenant table lives only in the main control DB, never in a tenant DB
# (mirrors app/services/tenant_service.py).
_EXCLUDE_TABLES = {"tenants"}


def _mk_engine(db_path: Path) -> Engine:
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA synchronous=NORMAL;")
        cur.execute("PRAGMA foreign_keys=ON;")
        cur.close()

    return engine


def _sqlite_column_ddl(column) -> str:
    """Build the ``ADD COLUMN`` fragment for a SQLAlchemy Column.

    Kept intentionally conservative: SQLite is loosely typed, so we emit the
    column name + a compiled type + NOT NULL/DEFAULT only when they are safe
    to add to an existing (possibly non-empty) table.
    """
    try:
        col_type = column.type.compile(dialect=__import__(
            "sqlalchemy.dialects.sqlite", fromlist=["dialect"]).dialect())
    except Exception:
        col_type = "TEXT"

    ddl = f'"{column.name}" {col_type}'

    # SQLite forbids adding a NOT NULL column without a default to a table
    # that may already contain rows. We only emit a DEFAULT clause when we can
    # produce a *literal* SQLite-safe value.
    #
    # IMPORTANT: Python-side callable defaults (e.g. ``default=datetime.utcnow``)
    # are applied by the ORM at INSERT time, NOT by SQLite. Emitting them as a
    # column DEFAULT produces invalid SQL like ``DEFAULT now()`` -> we must skip
    # them and leave the column nullable so the ALTER succeeds; the ORM fills
    # the value on every future insert regardless.
    default_sql = None

    # (a) server_default -> a real SQL expression we can pass through verbatim.
    if column.server_default is not None:
        arg = getattr(column.server_default, "arg", None)
        text_attr = getattr(arg, "text", None)
        if text_attr is not None:
            default_sql = str(text_attr)
        elif isinstance(arg, str):
            default_sql = arg

    # (b) a scalar (non-callable) python default -> safe to inline as a literal.
    elif column.default is not None and getattr(column.default, "is_scalar", False):
        val = column.default.arg
        if isinstance(val, bool):
            default_sql = "1" if val else "0"
        elif isinstance(val, (int, float)):
            default_sql = str(val)
        elif isinstance(val, str):
            default_sql = "'" + val.replace("'", "''") + "'"
        # anything else (dates, enums, callables) -> leave to the ORM.

    if default_sql is not None:
        ddl += f" DEFAULT {default_sql}"
        if not column.nullable:
            ddl += " NOT NULL"
    # No safe literal default -> keep the column NULLABLE so ADD COLUMN succeeds
    # on tables that may already hold rows. A manual migration can tighten the
    # constraint later if required (see DEPLOY.md § 10).

    return ddl


def upgrade_tenant_db(db_path: Path) -> tuple[int, int, int]:
    """Bring a single tenant DB up to the current schema.

    Returns (tables_created, columns_added, indexes_created).
    """
    engine = _mk_engine(db_path)
    tables_created = 0
    columns_added = 0

    # ── 1. Create any brand-new tables (idempotent) ──────────────────
    target_tables = [
        t for name, t in Base.metadata.tables.items()
        if name not in _EXCLUDE_TABLES
    ]
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    missing_tables = [t for t in target_tables if t.name not in existing_tables]
    if missing_tables:
        Base.metadata.create_all(bind=engine, tables=missing_tables)
        tables_created = len(missing_tables)
        for t in missing_tables:
            log.info("  + created table %s", t.name)

    # ── 2. Add missing columns to pre-existing tables ────────────────
    insp = inspect(engine)  # refresh after create_all
    existing_tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table in target_tables:
            if table.name not in existing_tables:
                continue  # freshly created above -> already current
            live_cols = {c["name"] for c in insp.get_columns(table.name)}
            for column in table.columns:
                if column.name in live_cols:
                    continue
                ddl = _sqlite_column_ddl(column)
                conn.execute(text(
                    f'ALTER TABLE "{table.name}" ADD COLUMN {ddl}'
                ))
                columns_added += 1
                log.info("  + added column %s.%s", table.name, column.name)

    # ── 3. Create missing INDEXES on pre-existing tables ─────────────
    # MEASURED DIVERGENCE 2026-08-19 -- why this step exists.
    #
    # Steps 1-2 reconciled tables and columns but NEVER indexes, so this
    # script and `alembic upgrade head` produced two DIFFERENT schemas from
    # the same source tree. Measured by running each path against its own
    # copy of the live tenant_acme.db after the doc_category migration:
    #
    #   alembic path : ix_audit_doc_category PRESENT
    #   ORM path     : ix_audit_doc_category MISSING   <-- the divergence
    #
    # The column was added by both, so nothing 500s and the drift is
    # invisible: the audit page still renders. What breaks is silent and
    # gets worse with scale -- the tenant that went through THIS script
    # does a full table scan on the one table guaranteed to grow forever,
    # so a query plan measured on the control DB does not describe the
    # tenants, and "the audit log is slow for one office" has no visible
    # cause. A deploy also can't be reasoned about, because which schema a
    # tenant ends up with depends on which script last touched it.
    #
    # Indexes are matched BY NAME against Base.metadata, which is exactly
    # why app/models/audit.py declares every index in __table_args__ with
    # an explicit name instead of `index=True`: a column-level index is
    # auto-named (ix_audit_logs_doc_category) and would NOT equal the
    # migration's name (ix_audit_doc_category), so this reconciliation
    # would create a SECOND, duplicate index rather than recognise the
    # existing one. Naming them identically in both places is what lets
    # these two paths converge.
    #
    # NOTE this script cannot be replaced by alembic and deleted: measured
    # on a legacy tenant DB with no alembic_version row, `alembic upgrade
    # head` fails outright with "table agents already exists" because it
    # replays the baseline revision against a populated database. The
    # ORM-diff path is the only thing that can bring such a DB forward,
    # so both paths must exist -- and therefore both must agree.
    insp = inspect(engine)  # refresh after the ALTERs
    indexes_created = 0
    with engine.begin() as conn:
        for table in target_tables:
            if table.name not in existing_tables and table not in missing_tables:
                continue
            try:
                live_idx = {i["name"] for i in insp.get_indexes(table.name)}
            except Exception as exc:  # noqa: BLE001 -- unreadable table, skip
                log.warning("  ! could not read indexes on %s: %s", table.name, exc)
                continue
            live_cols = {c["name"] for c in insp.get_columns(table.name)}
            for index in table.indexes:
                if index.name in live_idx:
                    continue
                # An index whose columns are not all present would raise;
                # that means the column reconciliation above could not add
                # them, which is a separate failure worth surfacing.
                idx_cols = {c.name for c in index.columns}
                if not idx_cols.issubset(live_cols):
                    log.warning(
                        "  ! skipping index %s: missing column(s) %s",
                        index.name, sorted(idx_cols - live_cols),
                    )
                    continue
                try:
                    index.create(bind=conn)
                    indexes_created += 1
                    log.info("  + created index %s on %s", index.name, table.name)
                except Exception as exc:  # noqa: BLE001 -- keep going
                    log.error("  ! index %s failed: %s", index.name, exc)

    engine.dispose()
    return tables_created, columns_added, indexes_created


def main() -> int:
    if not TENANTS_DIR.exists():
        log.info("No tenant directory at %s -- nothing to migrate.", TENANTS_DIR)
        return 0

    tenant_dbs = sorted(
        p for p in TENANTS_DIR.glob("tenant_*.db")
        if p.is_file() and "archive" not in p.parts
    )
    if not tenant_dbs:
        log.info("No tenant databases found in %s.", TENANTS_DIR)
        return 0

    log.info("Applying schema to %d tenant database(s)...", len(tenant_dbs))
    failures = 0
    total_tables = 0
    total_cols = 0
    total_idx = 0
    for db_path in tenant_dbs:
        log.info("→ %s", db_path.name)
        try:
            t, c, i = upgrade_tenant_db(db_path)
            total_tables += t
            total_cols += c
            total_idx += i
            if t == 0 and c == 0 and i == 0:
                log.info("  already up to date")
        except Exception as exc:  # noqa: BLE001 -- keep going past a bad DB
            failures += 1
            log.error("  FAILED: %s", exc)

    log.info(
        "Done. %d table(s) created, %d column(s) added, %d index(es) created "
        "across %d DB(s); %d failure(s).",
        total_tables, total_cols, total_idx, len(tenant_dbs), failures,
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
