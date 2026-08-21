# Automatic staged-database migration

`scripts/migrate_staged_database.py` copies the already-converted staging database into a current Dogar Portal schema. It is designed for a durable SQLite or PostgreSQL target and includes candidates, demands, clients, job categories, candidate assignments, and available lookup tables.

The command is **dry-run by default**. It never writes unless `--apply` is supplied. Inserts use conflict-safe primary/unique-key handling and do not overwrite existing rows. The `--verify` option compares source and target row counts after the copy. The script refuses unsupported database schemes and never uses a destructive drop/reset flag.

## Recommended workflow

First create a fresh durable target database and take a backup of any existing target. For PostgreSQL, point the application at the target database and run current Alembic migrations before importing. Do not use the free Render SQLite instance as the production target because its filesystem is non-persistent.

Run a dry run first:

```bash
python scripts/migrate_staged_database.py \
  --source-sqlite /home/ubuntu/work/data-staging/dogar_trading.db \
  --target-url 'postgresql+psycopg2://USER:PASSWORD@HOST/DATABASE' \
  --target-kind control \
  --dry-run
```

Apply and verify only after reviewing the planned tables:

```bash
python scripts/migrate_staged_database.py \
  --source-sqlite /home/ubuntu/work/data-staging/dogar_trading.db \
  --target-url 'postgresql+psycopg2://USER:PASSWORD@HOST/DATABASE' \
  --target-kind control \
  --apply --verify
```

For a single-office SQLite target, use a new durable file:

```bash
python scripts/migrate_staged_database.py \
  --source-sqlite /home/ubuntu/work/data-staging/dogar_trading.db \
  --target-url 'sqlite:////srv/durable-data/dogar_trading.db' \
  --target-kind single \
  --apply --verify
```

## Multi-tenant warning

The source staging database has no `tenants` rows and represents the converted single-office legacy dataset. Do not copy it into an arbitrary tenant database or combine it with another office’s data. For a multi-office PostgreSQL deployment, decide the destination tenant explicitly, migrate into that tenant database only, then verify candidate, demand, client, and assignment ownership before exposing the records through the portal. If the data must be split among offices, prepare a reviewed mapping before running the importer; this script intentionally does not guess tenant ownership.

The backup itself is a fixed point-in-time snapshot. New records or changes made after that snapshot are not included. After applying the import, compare counts and sample only approved non-sensitive keys, run the tenant-isolation test suite, rotate bootstrap credentials, and configure backups before production use.
