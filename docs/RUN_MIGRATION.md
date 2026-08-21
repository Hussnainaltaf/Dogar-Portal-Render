# SQLite → PostgreSQL migration runbook (Option A: DB-per-tenant)

This runbook covers a one-shot migration of the Dogar Trading Corporation
Portal from SQLite to PostgreSQL, **preserving the exact file-per-tenant
isolation model** — every SQLite database becomes its own PostgreSQL
database. Nothing is merged.

    data/dogar_trading.db                  →   postgres:  dogar_control
    data/tenants/tenant_<slug>.db          →   postgres:  tenant_<slug>

If a tenant lookup ever routes to the wrong DB, that tenant literally cannot
see another tenant's rows: the two databases share zero tables.

Alembic is now the sole authority for schema. `_safe_add_column` has been
retired to a deprecated no-op shim; **any schema change requires an Alembic
migration.**

---

## 0. Prerequisites

* **PostgreSQL ≥ 14** running and reachable
* PostgreSQL user with the `CREATEDB` privilege
* Python 3.11+ and the project's virtualenv at `./venv`
* SQLite source files present at their expected paths
  * `data/dogar_trading.db`             (control DB)
  * `data/tenants/tenant_<slug>.db`     (one per active tenant)
* `psycopg2-binary==2.9.12` installed (already in `requirements.txt`)
* `alembic==1.13.3` installed (already in `requirements.txt`)

Verify Postgres is up:

    pg_isready -h <PG_HOST> -p 5432
    psql "postgresql://<user>:<pw>@<PG_HOST>:5432/postgres" -c "SELECT version();"

---

## 1. Create the Postgres application user (once)

    sudo -u postgres psql <<'SQL'
    CREATE USER dogar WITH PASSWORD 'dogar_pw';
    ALTER USER dogar CREATEDB;
    SQL

`CREATEDB` is required so `provision_tenant_db` and `migrate_to_postgres.py`
can `CREATE DATABASE` new tenant DBs on demand.

---

## 2. Test the baseline migration on a scratch DB (recommended)

Prove the baseline applies cleanly BEFORE touching production data:

    createdb -O dogar test_baseline
    ALEMBIC_DATABASE_URL=postgresql://dogar:dogar_pw@localhost:5432/test_baseline \
      venv/bin/alembic upgrade head
    psql "postgresql://dogar:dogar_pw@localhost:5432/test_baseline" -c \
      "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public';"
    # Expected: 31 (30 app tables + alembic_version)
    dropdb test_baseline

---

## 3. Update .env

Add these variables (keep the existing SQLite `DATABASE_URL` line commented
out so you can roll back quickly):

    # DATABASE_URL=sqlite:///./data/dogar_trading.db   # OLD (kept for rollback)
    DATABASE_URL=postgresql://dogar:dogar_pw@<PG_HOST>:5432/dogar_control
    TENANT_DB_URL_TEMPLATE=postgresql://dogar:dogar_pw@<PG_HOST>:5432/tenant_{slug}

The `{slug}` placeholder in `TENANT_DB_URL_TEMPLATE` is **required**; the app
raises at startup if it is missing.

---

## 4. Dry-run the migration

    venv/bin/python scripts/migrate_to_postgres.py --dry-run \
        --source-sqlite=data/dogar_trading.db

Sample output:

    Source (control):  data/dogar_trading.db
    Target (control):  postgresql://…/dogar_control
    Template (tenants): postgresql://…/tenant_{slug}

    Control DB → Postgres
    Tenant 'acme' (data/tenants/tenant_acme.db) → postgresql://…/tenant_acme
    Tenant 'beta' (data/tenants/tenant_beta.db) → postgresql://…/tenant_beta

    [dry-run] No changes performed.

**Do not proceed if the tenant list is empty** — it means the control
SQLite couldn't be read.

---

## 5. Run the real migration

    venv/bin/python scripts/migrate_to_postgres.py \
        --source-sqlite=data/dogar_trading.db

Each destination DB is:
  1. Created (`CREATE DATABASE tenant_<slug>` via an AUTOCOMMIT admin conn).
  2. Brought to head via `alembic upgrade head` — this creates all 30
     tables and stamps `alembic_version = <baseline_revision>`.
  3. Every table then copied in FK-safe order, 500 rows per batched
     INSERT. SQLite 0/1 booleans are coerced to real Python `bool` for
     Postgres BOOLEAN columns.
  4. Primary-key sequences resynced with
     `setval(pg_get_serial_sequence('table','id'), max(id), true)`.

If you need to re-run from scratch, add `--force` — it drops the target
DB first. **Destructive; use only when you know what you're doing.**

    venv/bin/python scripts/migrate_to_postgres.py --force \
        --source-sqlite=data/dogar_trading.db

Migrate a single tenant only:

    venv/bin/python scripts/migrate_to_postgres.py --slug acme \
        --source-sqlite=data/dogar_trading.db

---

## 6. Verify parity

    venv/bin/python scripts/verify_postgres_parity.py

Compares row counts per table between every source SQLite and its
corresponding Postgres DB. Exits non-zero on drift. Skips
`alembic_version` and `sqlite_sequence` (SQLite-internal).

Example success:

    === control DB ===
      agent_cash: 33 rows OK
      candidates: 2675 rows OK
      ...
      tenants: 2 rows OK
    ...
    ✓ Parity verification PASSED for all databases.

If drift is reported, DO NOT switch traffic. Investigate first.

---

## 7. Bring the app up on Postgres

    pm2 restart dogar-portal --update-env
    curl -sS http://localhost:3000/healthz -w '%{http_code}\n'  # expect 200

Then run the shipped end-to-end HTTP integration suite:

    venv/bin/python /tmp/verify_postgres_integration.py
    # 14/14 checks passed

Manual smoke-test the browser flows:

  * Log in as an admin on the root portal → dashboard renders.
  * Log in on a tenant subdomain → only that tenant's data is visible.
  * Change a document template on tenant A, log out, log in as tenant B
    → tenant B does not see tenant A's changes.
  * Attempt a state-changing POST from a Postman/curl with no
    `X-CSRF-Token` → 403.

---

## 8. Provisioning NEW tenants on Postgres

`services/tenant_service.provision_tenant_db` is dual-backend: when
`TENANT_DB_URL_TEMPLATE` is set it will `CREATE DATABASE tenant_<slug>`
via the AUTOCOMMIT admin connection, then run `alembic upgrade head` on
the newly-created DB.

No schema-migration logic runs at request time in `app/core/tenancy.py`
anymore — the historical `Base.metadata.create_all()` call was removed.
If the DB is missing the required schema, Alembic will be the one to
create it, not lazy table auto-creation.

---

## 9. Ongoing schema changes (post-migration)

    # Modify a model in app/models/…
    ALEMBIC_DATABASE_URL=postgresql://dogar:dogar_pw@localhost:5432/dogar_control \
        venv/bin/alembic revision --autogenerate -m "add foo column"

    # Review the generated migration under alembic/versions/*.py, then:
    ALEMBIC_DATABASE_URL=postgresql://dogar:dogar_pw@localhost:5432/dogar_control \
        venv/bin/alembic upgrade head

    # Apply to every tenant DB:
    venv/bin/python scripts/apply_alembic_to_tenants.py

`alembic/env.py` is dual-URL — it accepts:
  1. `-x db_url=…` (CLI override, used by `apply_alembic_to_tenants.py`)
  2. `ALEMBIC_DATABASE_URL` env var
  3. `settings.DATABASE_URL` (fallback)

For tenant DBs, `include_object()` in `env.py` skips the `tenants` table
(that table lives only in the control DB).

---

## 10. Rollback

The rollback is fast because SQLite is untouched during the migration —
the copy is one-way, so the SQLite files remain the authoritative
snapshot.

    # 1. Stop the app
    pm2 stop dogar-portal

    # 2. Restore .env
    #    Uncomment the old sqlite DATABASE_URL, comment out the new
    #    Postgres one. Comment out TENANT_DB_URL_TEMPLATE.
    #
    #    DATABASE_URL=sqlite:///./data/dogar_trading.db
    #    # DATABASE_URL=postgresql://…/dogar_control
    #    # TENANT_DB_URL_TEMPLATE=postgresql://…/tenant_{slug}

    # 3. Restart on SQLite
    pm2 start dogar-portal --update-env
    curl -sS http://localhost:3000/healthz -w '%{http_code}\n'    # expect 200

Optional — drop the Postgres DBs afterwards to free space (destructive):

    for slug in $(psql -qtAX "postgresql://dogar:dogar_pw@localhost:5432/postgres" \
        -c "SELECT slug FROM (VALUES ('control'), ('acme'), ('beta')) AS t(slug);") ; do
      psql "postgresql://dogar:dogar_pw@localhost:5432/postgres" \
        -c "DROP DATABASE IF EXISTS tenant_${slug} WITH (FORCE);" || \
      psql "postgresql://dogar:dogar_pw@localhost:5432/postgres" \
        -c "DROP DATABASE IF EXISTS dogar_${slug} WITH (FORCE);"
    done

If you need to redo the migration after rollback, remember: the SQLite
files may have accumulated new rows since the last migration attempt
(anything written by the app while it was running on SQLite between
migration attempts). Use `--force` on the next migration run to drop and
re-copy from the (updated) SQLite source.

---

## 11. Known-safe verification matrix

The following automated checks are required to pass BEFORE switching
production traffic to Postgres:

| Check                                             | Command                                                            | Expected                       |
|---------------------------------------------------|--------------------------------------------------------------------|--------------------------------|
| Alembic baseline applies to fresh Postgres       | `alembic upgrade head` on `test_baseline`                          | 31 tables (30 app + alembic)   |
| Alembic baseline applies to fresh SQLite         | `alembic upgrade head` on scratch SQLite file                      | 31 tables                      |
| Row-count parity: control DB                     | `verify_postgres_parity.py`                                        | All tables OK                  |
| Row-count parity: every tenant                   | `verify_postgres_parity.py`                                        | All tables OK                  |
| HTTP integration: login + tenant isolation + CSRF+ RBAC (14 checks) | `verify_postgres_integration.py`                                   | 14/14 PASSED                   |
| Cross-tenant token replay blocked                | Included in integration suite                                       | acme cookie @ beta host → 401  |

---

## 12. Files touched by this migration

**Application code (dual-backend):**
* `app/db/session.py`             — engine config split for SQLite/Postgres
* `app/core/config.py`            — added `TENANT_DB_URL_TEMPLATE`
* `app/core/tenancy.py`           — `tenant_db_url()`, `is_postgres_tenants()`, dual-backend sessionmaker cache; removed `create_all()` call
* `app/services/tenant_service.py`— dual-backend `provision_tenant_db`; `_create_postgres_database`; `_stamp_alembic_head_on`
* `app/db/init_db.py`             — `_safe_add_column` → deprecated shim; `create_tables()` runs Alembic instead of ad-hoc DDL
* `requirements.txt`              — enabled `psycopg2-binary==2.9.12`

**Alembic:**
* `alembic.ini`                   — blank `sqlalchemy.url` (env.py resolves)
* `alembic/env.py`                — dual-URL, tenant-aware `include_object`, batch mode on SQLite
* `alembic/script.py.mako`        — added `import app.db.types`
* `alembic/versions/1c1e09f245c6_initial_schema_baseline.py`
                                  — 30-table baseline

**Scripts:**
* `scripts/migrate_to_postgres.py`   — CLI migration tool
* `scripts/apply_alembic_to_tenants.py` — apply Alembic head to every tenant
* `scripts/verify_postgres_parity.py`   — row-count parity verifier

**Docs:**
* `docs/RUN_MIGRATION.md`         — this file
