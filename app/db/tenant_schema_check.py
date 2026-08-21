"""Detect tenant databases whose schema is behind the running code.

WHY THIS EXISTS (measured 2026-08-19)
-------------------------------------
The control database is migrated automatically: ``app.main._lifespan`` calls
``run_schema_migrations()`` (``alembic upgrade head``) on every boot. Tenant
databases are NOT -- each tenant is a separate SQLite file (or a separate
Postgres database), and they are rolled forward by
``docker-entrypoint.sh`` step 2, which only runs in the container image.

Start the app any other way -- uvicorn directly, PM2, a systemd unit, a
developer machine -- and the control DB races ahead while every tenant DB
stays behind. Measured on this deployment:

    control DB      -> a1c6e8b4d072   (upgraded on boot)
    tenant_acme.db  -> 9b5e7d3f2c81   (untouched)
    tenant_beta.db  -> 9b5e7d3f2c81   (untouched)

and the consequence, measured with the real ORM query the audit endpoint
issues:

    sqlite3.OperationalError: no such column: audit_logs.doc_category

That is an HTTP 500 on a page that works perfectly for the default office,
with nothing in the logs explaining why -- the operator sees "the portal is
broken for Acme" and no cause.

WHY THIS ONLY WARNS
-------------------
Auto-upgrading every tenant inside the boot path was rejected:

  * boot time and boot failure modes would scale with tenant count;
  * a tenant upgrade interrupted mid-way (OOM, container kill) leaves a
    half-migrated database with no operator present;
  * the app process may legitimately lack write access to tenant storage.

A loud, specific, actionable warning is the correct behaviour: it names each
lagging tenant, its revision, and the exact command to fix it.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("dtc.migrate")

FIX_COMMAND = (
    "venv/bin/python scripts/apply_alembic_to_tenants.py "
    "&& venv/bin/python scripts/apply_tenant_migrations.py"
)


def _head_revision() -> str | None:
    """The revision this code base expects, or None if alembic is absent."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
    except Exception:  # noqa: BLE001 -- alembic not installed in this build
        return None

    project_root = Path(__file__).resolve().parents[2]
    ini = project_root / "alembic.ini"
    if not ini.exists():
        return None
    try:
        return ScriptDirectory.from_config(Config(str(ini))).get_current_head()
    except Exception as exc:  # noqa: BLE001
        log.debug("could not resolve alembic head: %s", exc)
        return None


def _tenant_revision(url: str) -> str | None:
    """The revision a tenant DB is stamped at, or None if unreadable."""
    import sqlalchemy as sa

    engine = sa.create_engine(url)
    try:
        with engine.connect() as conn:
            rows = list(conn.execute(sa.text("SELECT version_num FROM alembic_version")))
        return rows[0][0] if rows else None
    except Exception:  # noqa: BLE001 -- no alembic_version table, or no DB
        return None
    finally:
        engine.dispose()


def warn_if_tenants_behind() -> list[tuple[str, str | None]]:
    """Log a warning for every tenant DB that is not at the code's head.

    Returns the list of ``(slug, revision)`` pairs found to be behind, so
    tests can assert on the detection instead of scraping log output.
    """
    head = _head_revision()
    if head is None:
        log.debug("tenant schema check skipped: no alembic head available")
        return []

    from app.core.tenancy import tenant_db_url
    from app.db.session import SessionLocal
    from app.models.tenant import Tenant

    behind: list[tuple[str, str | None]] = []
    db = SessionLocal()
    try:
        tenants = [(t.slug, t.db_path, t.status or "") for t in db.query(Tenant).all()]
    except Exception as exc:  # noqa: BLE001 -- tenants table may not exist yet
        log.debug("tenant schema check skipped: %s", exc)
        return []
    finally:
        db.close()

    for slug, db_path, _status in tenants:
        url = tenant_db_url(slug, db_path)
        # A SQLite tenant whose file was archived by a delete has nothing to
        # check -- and must NOT be "checked", because connecting would create
        # an empty database at that path.
        if url.startswith("sqlite"):
            if not Path(url.split("///", 1)[-1]).exists():
                continue
        rev = _tenant_revision(url)
        if rev != head:
            behind.append((slug, rev))

    if behind:
        listing = ", ".join(f"{slug}@{rev or 'unstamped'}" for slug, rev in behind)
        log.warning(
            "TENANT SCHEMA DRIFT: %d tenant database(s) are behind the running "
            "code (expected %s): %s. Requests scoped to those offices can fail "
            "with 'no such column' errors even though the default office works. "
            "Fix with: %s",
            len(behind), head, listing, FIX_COMMAND,
        )
    else:
        log.info("Tenant schema check: all tenant databases at %s.", head)
    return behind
