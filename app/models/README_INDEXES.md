# Index declaration rules (read before adding or changing an index)

**Measured 2026-08-19.** This project builds a database schema by **two**
different routes, and both are load-bearing. Every index must therefore be
declared in **both** places, with **exactly the same name**.

## The two routes

| Route | Used by | Mechanism |
|---|---|---|
| **A. Alembic** | existing DBs; `docker-entrypoint.sh` step 1; `app.db.init_db.run_schema_migrations()` (also runs on app startup) | `alembic upgrade head` |
| **B. ORM metadata** | brand-new DBs (`create_tables()` → `create_all()` + `alembic stamp head`); every tenant DB via `scripts/apply_tenant_migrations.py` (entrypoint step 2) | `Base.metadata.create_all()` + an index/column diff |

Route B **cannot** be replaced by route A. Measured against a legacy tenant DB
with no `alembic_version` row:

```
$ alembic -x db_url=sqlite:///tenant_legacy.db upgrade head
sqlite3.OperationalError: table agents already exists
```

Alembic replays the baseline revision against a populated database and dies.
The ORM-diff reconciler is the only thing that can bring such a DB forward.

Route A **cannot** be replaced by route B either: `create_all()` cannot alter
an existing table, backfill data, or create a trigger.

**So both routes exist permanently, and both must agree.**

## Why a fresh install silently lost indexes

A brand-new database goes `create_all()` → `alembic stamp head`. `stamp` marks
every revision as applied **without running any of them**. So a fresh install
never executes a migration body — and any index created only inside a
migration is **absent forever** on that install.

Measured on this repo before the fix: a fresh install was missing **9**
indexes that a migrated install had, including all six performance indexes
from `2a4f1b8c9d31`, whose own docstring records the measured wins:

```
reminders feed    1.66ms -> 0.17ms   (-89%)
demands list      0.65ms -> 0.15ms   (-77%)
clients list      0.33ms -> 0.12ms   (-63%)
```

A new customer therefore deployed straight into the *un-optimised* schema
that migration was written to fix, and nothing reported it — a missing index
never raises, it just scans.

## Why names must match exactly

`scripts/apply_tenant_migrations.py` reconciles indexes **by name**. Two
consequences:

1. If a migration creates `ix_audit_doc_category` but the model uses
   `index=True` (auto-named `ix_audit_logs_doc_category`), the reconciler does
   not recognise the existing index and adds a **second, duplicate** index —
   on every deploy, forever.
2. If the model declares nothing at all, the reconciler cannot repair a tenant
   that is missing the index.

Measured example of exactly this split:

```
migrated DB        : ix_candidates_latest_demand_sort   (composite, raw SQL in 3b7d2e4f8a15)
fresh create_all() : ix_candidates_latest_demand_id     (single col, from index=True)
```

Both served the list `ORDER BY` as a covering scan, so nothing ever failed —
which is why it survived undetected.

## Rules

1. **Never use `index=True`** for an index a migration also creates. Declare it
   in `__table_args__` with the migration's exact name.
2. **`index=True` is fine** only when no migration touches that index and the
   auto-generated `ix_<table>_<column>` name is what the migration would use.
3. When you add an index in a migration, **add the identical declaration to the
   model** in the same commit.
4. Use `sqlalchemy.text("col DESC")` to reproduce a `DESC` index faithfully.
5. Run the guard tests:

```bash
venv/bin/python -m pytest tests/test_schema_convergence.py -o addopts= -v
```

`tests/test_schema_convergence.py` builds one DB by each route and asserts
they converge, and that every model-declared index exists in the migrated DB.
It fails if this convention is broken again.
