"""Alembic env for the multi-tenant portal.

The migration environment supports THREE run-modes:

1.  **Default control-DB mode** (no ``-x`` args): reads
    ``settings.DATABASE_URL`` from the app config and migrates the
    control database (``data/dogar_trading.db`` in SQLite dev, or the
    control Postgres DB in production).

2.  **Explicit URL override** (``alembic -x db_url=<url>``): migrate an
    arbitrary DB. Used by ``scripts/apply_alembic_to_tenants.py`` and
    by the runbook to migrate one tenant at a time. Example::

        alembic -x db_url=postgresql://user:pw@host:5432/tenant_acme upgrade head

3.  **All-tenants mode** (``alembic -x all_tenants=1``): iterates every
    row in the control DB's ``tenants`` table, computing the URL via
    ``tenant_db_url(slug, db_path)``, and runs migrations against each.
    Used by ``scripts/apply_alembic_to_tenants.py``.

The choice of dialect (sqlite / postgres) is derived from the URL --
no separate ``sqlite.env`` / ``postgres.env`` split needed. Note that
SQLite migrations MUST run in ``render_as_batch=True`` mode to emulate
the ``ALTER TABLE`` operations Postgres supports natively.
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, create_engine, pool

from alembic import context

# ── Make the app package importable ───────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# Import the app configuration + models AFTER we adjust sys.path.
from app.core.config import settings  # noqa: E402
from app.db.session import Base  # noqa: E402
import app.models  # noqa: E402, F401  -- register every model on Base.metadata

# ── Alembic Config object ─────────────────────────────────────────────────────
config = context.config

if config.config_file_name is not None:
    # `disable_existing_loggers=False` is NOT optional here.
    #
    # AUDIT 2026-08-01 (High, found while fixing the status-stream mapping):
    # app/main.py::_on_startup() calls run_schema_migrations(), which invokes
    # `command.upgrade(cfg, "head")` IN-PROCESS. That imports this module and
    # runs fileConfig(), whose `disable_existing_loggers` argument defaults to
    # True. alembic.ini declares only root/sqlalchemy/alembic, so every other
    # logger that already existed was flipped to `disabled = True` -- for the
    # entire lifetime of the process, on every boot.
    #
    # Measured before this fix: 14 of 18 `dtc.*` loggers dead after startup,
    # including dtc.security, dtc.auth, dtc.rbac, dtc.exception, dtc.tenancy and
    # dtc.pdf_engine. The practical effect is that the application could not
    # report anything about itself in production: security and RBAC decisions,
    # unhandled-exception detail, tenant resolution problems and the overlay
    # engine's own "this field is off the page" warning were all silently
    # dropped. Only loggers created *after* startup (dtc.request) survived,
    # which is why request logs looked fine and made the gap easy to miss.
    #
    # This one line is why defects in this app appeared to "come back
    # silently" -- nothing was ever able to say otherwise.
    # Pinned by tests/test_logging_not_muted.py.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Metadata that autogenerate compares migrations against. We exclude the
# ``tenants`` table when running against a TENANT database (it lives only
# on the control DB) -- that gate is applied in ``include_object``.
target_metadata = Base.metadata


def _resolve_url() -> str:
    """Compute the database URL for this Alembic run.

    Precedence:
      1. ``-x db_url=…`` command-line arg.
      2. ``ALEMBIC_DATABASE_URL`` environment variable.
      3. ``settings.DATABASE_URL`` (i.e. the app's control DB).
    """
    x_args = context.get_x_argument(as_dictionary=True)
    if "db_url" in x_args and x_args["db_url"]:
        return x_args["db_url"]
    env_url = os.environ.get("ALEMBIC_DATABASE_URL")
    if env_url:
        return env_url
    return settings.DATABASE_URL


def _is_tenant_url(url: str) -> bool:
    """Heuristic: URLs the migration runner treats as tenant DBs.

    * Any URL where the path/database name contains ``tenant_`` (both
      ``sqlite:///…/tenant_acme.db`` and ``postgresql://…/tenant_acme``).
    * Explicit override via ``-x is_tenant=1`` / ``ALEMBIC_IS_TENANT=1``.
    """
    x_args = context.get_x_argument(as_dictionary=True)
    if x_args.get("is_tenant") in ("1", "true", "yes"):
        return True
    if os.environ.get("ALEMBIC_IS_TENANT") in ("1", "true", "yes"):
        return True
    return "tenant_" in url


def include_object(obj, name, type_, reflected, compare_to):
    """Skip the ``tenants`` table when migrating tenant DBs.

    The ``tenants`` table (defined by ``app.models.tenant.Tenant``) is
    the control-DB registry; it MUST NOT exist inside a tenant's own DB.
    Alembic autogenerate would otherwise try to create it in every
    tenant migration.
    """
    if _is_tenant_url(_resolve_url()) and type_ == "table" and name == "tenants":
        return False
    return True


def _render_as_batch(url: str) -> bool:
    """Enable batch operations on SQLite so ALTER TABLE-style migrations work.

    Postgres supports ALTER TABLE natively -- batch mode is unnecessary and
    slightly slower there.
    """
    return url.startswith("sqlite")


# ────────────────────────────────────────────────────────────────────────────
# Offline mode
# ────────────────────────────────────────────────────────────────────────────
def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL to stdout, no DB conn)."""
    url = _resolve_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        render_as_batch=_render_as_batch(url),
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ────────────────────────────────────────────────────────────────────────────
# Online mode
# ────────────────────────────────────────────────────────────────────────────
def run_migrations_online() -> None:
    """Run migrations against a live DB connection."""
    # Support the case where a caller (tenant_service._stamp_alembic_head_on)
    # already opened a connection and stashed it on the config attributes.
    connectable = config.attributes.get("connection")

    if connectable is None:
        url = _resolve_url()
        connectable = create_engine(url, poolclass=pool.NullPool)
        _configure_and_run(connectable, url)
    else:
        # Connection provided by caller. Use its engine URL for dialect detection.
        url = str(connectable.engine.url)
        context.configure(
            connection=connectable,
            target_metadata=target_metadata,
            include_object=include_object,
            render_as_batch=_render_as_batch(url),
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


def _configure_and_run(engine, url: str) -> None:
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            render_as_batch=_render_as_batch(url),
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
