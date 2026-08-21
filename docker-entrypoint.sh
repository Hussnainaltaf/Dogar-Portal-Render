#!/bin/sh
# ===========================================================================
# Dogar Portal — container entrypoint
# ---------------------------------------------------------------------------
# Runs schema migrations BEFORE the web server starts, then execs uvicorn so
# it becomes PID 1 (clean signal handling / graceful shutdown).
#
# Order of operations (matches DEPLOY.md § 10):
#   1. alembic upgrade head            -> migrate the MAIN control DB
#                                         (skipped gracefully if Alembic is
#                                          not configured in this build; the
#                                          app's own idempotent seeders in
#                                          _on_startup() then handle the main
#                                          DB schema).
#   2. apply_tenant_migrations.py      -> roll the same schema out to every
#                                         per-tenant SQLite DB
#                                         (data/tenants/tenant_*.db).
#   3. exec uvicorn ...                -> start the API server.
#
# A failure in step 1 or 2 aborts startup (set -e) so a broken migration is
# loud rather than silently serving a half-migrated schema.
# ===========================================================================
set -e

echo "[entrypoint] === Dogar Portal startup ==="

# ── 1. Main control-DB migrations (Alembic, if present) ────────────────────
if [ -f "alembic.ini" ] && command -v alembic >/dev/null 2>&1; then
    echo "[entrypoint] Running: alembic upgrade head"
    alembic upgrade head
else
    echo "[entrypoint] Alembic not configured in this build -- skipping 'alembic upgrade head'."
    echo "[entrypoint] (Main DB schema is applied by the app's idempotent startup seeders.)"
fi

# ── 2. Per-tenant SQLite schema roll-out ───────────────────────────────────
echo "[entrypoint] Running: python scripts/apply_tenant_migrations.py"
python scripts/apply_tenant_migrations.py

# ── 3. Hand off to the web server (PID 1) ──────────────────────────────────
# AUDIT L-6 (2026-07-10): --workers 1 is REQUIRED with the default SQLite
# backend. SQLite is a single-writer, file-based engine; running multiple
# uvicorn workers against one .db file causes "database is locked" errors and
# in-process caches / rate-limit state that diverge between workers. To scale
# horizontally, migrate to PostgreSQL (set DATABASE_URL / TENANT_DB_URL_TEMPLATE
# — see DEPLOY.md) and only THEN raise --workers (or run multiple replicas).
echo "[entrypoint] Starting uvicorn (workers=1 — SQLite single-writer; see DEPLOY.md L-6)..."
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 3000 \
    --workers 1 \
    --forwarded-allow-ips 127.0.0.1
