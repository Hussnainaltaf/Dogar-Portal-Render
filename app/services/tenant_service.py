"""Tenant provisioning service.

Creates an isolated database for a new portal instance ("Copy Portal" for
another business partner), seeds it with the same schema as the main app,
and creates a fresh admin user inside it.

Storage backends
----------------
* SQLite (default, dev): one file per tenant at
  ``data/tenants/tenant_<slug>.db``.
* PostgreSQL (production): one database per tenant, named
  ``tenant_<slug>``, on the server described by
  ``settings.TENANT_DB_URL_TEMPLATE``. The template is expected to
  point at ``…/tenant_{slug}`` so the same URL that the runtime uses
  also identifies the DB to create.

Under both backends the CALLING contract is identical: pass
``slug``, get back a ``db_path`` string that is safe to store on
``tenants.db_path``. For Postgres the ``db_path`` is the full
SQLAlchemy URL, so the ``tenants`` table row is still self-contained
and portable across environments.
"""
import os
import re
import shutil
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401  -- ensures all models are registered on Base
from app.core.security import hash_password
from app.core.tenancy import is_postgres_tenants, tenant_db_url
from app.db.session import Base

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "tenants"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name.lower()).strip("-")
    return s[:40] or "tenant"


def tenant_db_path(slug: str) -> str:
    return str(DATA_DIR / f"tenant_{slug}.db")


def _default_file_prefix_for(slug: str, short_name: str = "") -> str:
    """Compute a tenant-safe file-number prefix at provisioning time.

    M-2 FIX (audit 2026-07-03): the CompanySettings model default is
    ``DTC/786/`` (kept for backward compatibility with the primary Dogar
    tenant whose historical rows already display with that prefix). If a
    NEW tenant inherits that default, its printed receipts / demand files
    look like they came from Dogar Trading — a real branding leak.

    Preference order:
      1. First non-empty uppercase alphanumerics of ``short_name`` (max 8 chars).
      2. Otherwise the slug transformed the same way.
      3. Absolute fallback: empty string (no prefix), NEVER "DTC/786/".

    The trailing "/" is always appended so downstream ``_get_prefix``
    treats it consistently (see app/api/endpoints/demands.py).
    """
    for source in (short_name, slug):
        if not source:
            continue
        cleaned = re.sub(r"[^A-Za-z0-9]", "", source).upper()[:8]
        if cleaned:
            return f"{cleaned}/"
    return ""


def _create_postgres_database(slug: str) -> str:
    """Issue ``CREATE DATABASE tenant_<slug>`` on the server described by
    ``settings.TENANT_DB_URL_TEMPLATE`` and return the tenant's URL.

    ``CREATE DATABASE`` cannot run inside a transaction and must be issued
    against the SERVER (not the target DB), so we connect to the default
    maintenance database ``postgres`` on the same host using the template's
    credentials, and use AUTOCOMMIT isolation.
    """
    target_url = tenant_db_url(slug, "")
    parsed = urlparse(target_url)
    db_name = parsed.path.lstrip("/")
    if not db_name:
        raise ValueError(f"tenant URL has no database name: {target_url}")
    server_url = urlunparse(parsed._replace(path="/postgres"))
    server_engine = create_engine(server_url, isolation_level="AUTOCOMMIT")
    try:
        with server_engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"),
                {"n": db_name},
            ).scalar()
            if exists:
                raise ValueError(f"Tenant Postgres DB already exists: {db_name}")
            if not re.fullmatch(r"[A-Za-z0-9_\-]+", db_name):
                raise ValueError(f"Refusing to CREATE DATABASE with unsafe name {db_name!r}")
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    finally:
        server_engine.dispose()
    return target_url


def provision_tenant_db(slug: str, admin_email: str, admin_password: str,
                        admin_name: str, company_name: str,
                        short_name: str = "") -> str:
    """Provision a fresh isolated tenant DB, create schema, seed admin user.

    * Postgres backend: creates a new database ``tenant_<slug>`` on the
      server described by ``settings.TENANT_DB_URL_TEMPLATE``. Returns the
      full SQLAlchemy URL (stored on ``tenants.db_path``).
    * SQLite backend (default): creates ``data/tenants/tenant_<slug>.db``
      and returns the absolute file path.
    """
    postgres = is_postgres_tenants()

    if postgres:
        db_url = _create_postgres_database(slug)
        db_path_for_row = db_url
        engine = create_engine(
            db_url,
            connect_args={"connect_timeout": 10},
            pool_pre_ping=True,
        )
    else:
        db_path_for_row = tenant_db_path(slug)
        if os.path.exists(db_path_for_row):
            raise ValueError(f"Tenant DB already exists: {db_path_for_row}")
        db_url = f"sqlite:///{db_path_for_row}"
        engine = create_engine(
            db_url,
            connect_args={"check_same_thread": False, "timeout": 30},
        )

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL;")
            cur.execute("PRAGMA synchronous=NORMAL;")
            cur.execute("PRAGMA foreign_keys=ON;")
            cur.close()

    # Create all tables (except the Tenant table itself -- that lives in the
    # main control DB). Safe on freshly-created DBs; no-op on existing tables.
    tables = [t for name, t in Base.metadata.tables.items() if name != "tenants"]
    Base.metadata.create_all(bind=engine, tables=tables)

    # Stamp Alembic to head so `alembic upgrade` starts at the right revision.
    try:
        _stamp_alembic_head_on(engine)
    except Exception as exc:                                  # noqa: BLE001
        import logging
        logging.getLogger("dtc.tenant").warning(
            "Alembic stamp failed for tenant %r: %s (non-fatal)", slug, exc,
        )

    # Seed admin user in the new tenant DB
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()
    try:
        from app.models import CompanySettings, User
        admin = User(
            name=admin_name or "Administrator",
            email=admin_email,
            password_hash=hash_password(admin_password),
            role="admin",
            is_active=True,
        )
        db.add(admin)

        # Seed company settings row.
        # M-2 FIX: explicitly set file_prefix, slug, and subdomain so this
        # tenant does NOT inherit the model-level "DTC/786/" default (which
        # would make its receipts look like they came from Dogar Trading).
        #
        # BUGFIX (audit 2026-07-07): CompanySettings has NO `short_name` column
        # (short_name lives on the Tenant model, not CompanySettings — see
        # app/models/lookups.py::CompanySettings vs app/models/tenant.py). The
        # previous `short_name=_short` kwarg raised TypeError on every call,
        # which the broad `except (TypeError, AttributeError)` below swallowed —
        # so CompanySettings was NEVER seeded for a new tenant and every new
        # portal silently fell back to the "DTC/786/" default prefix. `_short`
        # is still used to derive the file_prefix; it is no longer passed to the
        # constructor.
        try:
            _short = (short_name or company_name[:40]).strip() or company_name[:40]
            cs = CompanySettings(
                company_name=company_name,
                slug=slug,
                subdomain=slug,
                file_prefix=_default_file_prefix_for(slug, _short),
                starting_point=0,
            )
            db.add(cs)
        except (TypeError, AttributeError) as exc:
            # CompanySettings might have different columns on legacy DBs -- non-fatal.
            import logging
            logging.getLogger("dtc.tenant").warning(
                "Could not seed CompanySettings for new tenant: %s", exc
            )

        # BUGFIX (audit 2026-08-09): provisioning previously seeded only the
        # admin user + company settings but NEVER the system roles. Every new
        # tenant portal therefore opened the "Roles & Access Control" screen
        # with an EMPTY roles table (GET /api/users/roles -> []), so the RBAC
        # editor had nothing to list and the "Customize Role" modal had no
        # preset roles to base a matrix on. Reuse the canonical role seeder so
        # every tenant gets Admin/Manager/Staff with the correct permission
        # presets, exactly like the root portal.
        try:
            from app.db.init_db import seed_roles
            seed_roles(db)
        except Exception as exc:                                 # noqa: BLE001
            import logging
            logging.getLogger("dtc.tenant").warning(
                "Could not seed system roles for new tenant %r: %s", slug, exc
            )

        db.commit()
    finally:
        db.close()

    return db_path_for_row


def _stamp_alembic_head_on(engine) -> None:
    """Best-effort ``alembic stamp head`` against an arbitrary engine.

    Uses Alembic's programmatic API so we can target a freshly-provisioned
    tenant DB (rather than the default ``sqlalchemy.url`` from alembic.ini).
    We locate the ``alembic.ini`` relative to the project root; if not
    present we skip -- callers of ``provision_tenant_db`` treat this as
    non-fatal.
    """
    import logging

    from alembic import command
    from alembic.config import Config
    from app.db.init_db import _restore_root_logging  # noqa: PLC0415

    project_root = Path(__file__).resolve().parents[2]
    ini = project_root / "alembic.ini"
    if not ini.exists():
        return
    cfg = Config(str(ini))
    # Direct override so Alembic uses the tenant engine, not alembic.ini's URL.
    cfg.attributes["connection"] = engine.connect()

    # MEASURED 2026-08-19: `command.stamp()` loads alembic/env.py exactly like
    # `command.upgrade()` does, so it runs fileConfig() and rebuilds the ROOT
    # logger from alembic.ini (`[logger_root] level = WARN`, plain console
    # handler). This function runs INSIDE the live web app -- provisioning a
    # tenant from the admin UI would therefore mute every INFO log for the rest
    # of the process and replace the JSON formatter the log pipeline parses.
    # See the full note in app/db/init_db.py::run_schema_migrations.
    root = logging.getLogger()
    saved_level = root.level
    saved_handlers = list(root.handlers)
    try:
        command.stamp(cfg, "head")
    finally:
        _restore_root_logging(saved_level, saved_handlers)
        conn = cfg.attributes.get("connection")
        if conn is not None:
            conn.close()


def archive_tenant_db(db_path: str) -> str:
    """Move tenant DB file to /data/tenants/archive/ -- used when deleting."""
    if not os.path.exists(db_path):
        return ""
    archive_dir = DATA_DIR / "archive"
    archive_dir.mkdir(exist_ok=True)
    dest = archive_dir / Path(db_path).name
    shutil.move(db_path, dest)
    return str(dest)
