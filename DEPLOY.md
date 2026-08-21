# Deployment Guide — Dogar Global Recruitment Portal

**Developed by HussnainTechVertex Pvt Ltd.**  
**Version:** 1.0 (Post-Audit Production Release)  
**Requires:** Python 3.11+ · SQLite 3.39+ · 512 MB RAM minimum

---

## Prerequisites Checklist

Before deploying, confirm:

- [ ] `.env` file created from `.env.example` with all values filled in
- [ ] `SECRET_KEY` generated: `python3 -c "import secrets; print(secrets.token_hex(32))"`
- [ ] `VENDOR_ADMIN_EMAIL` and `VENDOR_ADMIN_PASSWORD` set (16+ chars)
- [ ] `DEFAULT_ADMIN_PASSWORD` set (12+ chars, not a dictionary word)
- [ ] `ENV=production` in your `.env`
- [ ] Running behind nginx or Cloudflare (HTTPS required for secure cookies)
- [ ] Backups scheduled for `data/` directory and `app/static/uploads/`

---

## 1) Quick Start (Development Sandbox)

```bash
# 1. Extract and enter the directory
unzip dogar-portal-patched.zip
cd dogar-portal

# 2. Create a Python virtual environment (Python 3.11+ required)
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
# To also run the test suite, install the dev deps (pytest/httpx/openpyxl/ruff/
# playwright) which live in a SEPARATE file — requirements.txt alone is NOT
# enough to run pytest:
#   pip install -r requirements.txt -r requirements-dev.txt
#   TESTING=true pytest -q        # matches CI (.github/workflows/ci.yml)

# 4. Configure environment
cp .env.example .env
# Edit .env — set SECRET_KEY and admin credentials

# 5. Start (development mode — API docs enabled at /api/docs)
uvicorn app.main:app --host 0.0.0.0 --port 3000 --reload

# 6. Open http://localhost:3000
#    Log in with DEFAULT_ADMIN_EMAIL / DEFAULT_ADMIN_PASSWORD from .env
#    You will be prompted to change the password on first login.
```

---

## 2) Production with PM2 (Bare-Metal / VPS)

```bash
# Install PM2 (requires Node.js 18+)
npm install -g pm2

# Create and populate .env
cp .env.example .env
nano .env          # Set SECRET_KEY, passwords, VENDOR_ADMIN_*

# Start the portal daemon
pm2 start ecosystem.config.cjs
pm2 save

# Auto-start on system reboot
pm2 startup
# Follow the printed instructions (run the sudo command it gives you)

# Monitor
pm2 logs dogar-portal --nostream
pm2 status
```

**Note:** `ecosystem.config.cjs` ships with `ENV: 'production'`. The app will refuse to
start if `SECRET_KEY` or `DEFAULT_ADMIN_PASSWORD` are left as weak defaults.

---

## 3) Docker (Recommended for Production)

```bash
# 1. Configure environment
cp .env.example .env
nano .env    # Fill in SECRET_KEY, passwords, VENDOR_ADMIN_* etc.

# 2. Build the image
docker build -t dogar-portal:latest .

# 3. Start with docker compose (recommended — manages data volumes)
docker compose up -d

# 4. Check health
docker compose ps
docker compose logs -f portal

# 5. Stop
docker compose down
```

The `docker-compose.yml` mounts four persistent volumes:
- `dogar-data` → SQLite database (`data/`)
- `dogar-uploads` → Candidate photos, OCR files (`app/static/uploads/`)
- `dogar-generated` → Generated PDFs (`app/static/uploads/generated_pdfs/`)
- `dogar-pdf-bg` → Uploaded PDF letterhead/background scans (`app/static/pdf_backgrounds/`) — **audit H-2**

These volumes survive container restarts and rebuilds. **Back them up regularly.**

> ⚠️ **Single-worker constraint (audit L-6).** The default SQLite backend is a
> single-writer, file-based engine, so the app runs uvicorn with `--workers 1`
> (see `docker-entrypoint.sh`). Running multiple workers/replicas against one
> SQLite file causes `database is locked` errors and divergent in-process
> caches. To scale out, migrate to **PostgreSQL** (set `DATABASE_URL` /
> `TENANT_DB_URL_TEMPLATE`, see §10 and `scripts/migrate_to_postgres.py`) and
> only then increase `--workers` or add replicas.

---

## 4) Behind Nginx + HTTPS (Required for Production)

The secure cookie (`httponly`, `samesite=lax`, `secure=True`) only transmits over
HTTPS. **HTTP-only deployments will break authentication.**

```nginx
server {
    listen 80;
    server_name portal.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name portal.example.com;

    ssl_certificate     /etc/letsencrypt/live/portal.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/portal.example.com/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    client_max_body_size 16M;

    # Serve static assets directly from nginx (bypasses Python entirely)
    location /static/ {
        alias /opt/dogar-portal/app/static/;
        expires 30d;
        access_log off;
        add_header Cache-Control "public, max-age=2592000, immutable";
    }

    location / {
        proxy_pass         http://127.0.0.1:3000;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_read_timeout 120s;
    }
}
```

**Install HTTPS certificate:**
```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d portal.example.com
```

---

## 5) Environment Variables Reference

See `.env.example` for the full annotated list. Required variables:

| Variable | Required | Description |
|---|---|---|
| `ENV` | Yes | `production` on live servers |
| `SECRET_KEY` | Yes | 32+ char hex string. Generate: `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `DEFAULT_ADMIN_EMAIL` | Yes | Bootstrap admin email |
| `DEFAULT_ADMIN_PASSWORD` | Yes | Bootstrap admin password (12+ chars) |
| `VENDOR_ADMIN_EMAIL` | Yes (prod) | Vendor support account email |
| `VENDOR_ADMIN_PASSWORD` | Yes (prod) | Vendor support account password (16+ chars) |
| `COMPANY_NAME` | No | Displayed in header and PDFs |
| `OPENAI_API_KEY` | No | Enables the DtcBot AI chatbot |

---

## 6) First Login

1. Navigate to `https://portal.example.com`
2. Log in with `DEFAULT_ADMIN_EMAIL` / `DEFAULT_ADMIN_PASSWORD` (set in `.env`)
3. You will be immediately redirected to the password-change screen
4. Set a strong new password (12+ characters, not on the weak-list)
5. The `must_change_password` flag is cleared and you have full access

---

## 7) Backup & Recovery

**Daily backup script (add to cron):**
```bash
#!/bin/bash
DATE=$(date +%F)
BACKUP_DIR=/opt/backups/dogar-portal

mkdir -p "$BACKUP_DIR"

# 1. SQLite database (safe hot-copy using SQLite's backup API)
sqlite3 ./data/dogar_trading.db ".backup $BACKUP_DIR/dogar_trading_$DATE.db"

# 2. File uploads (photos, generated PDFs)
tar -czf "$BACKUP_DIR/uploads_$DATE.tar.gz" app/static/uploads/

# 3. Retain 30 days
find "$BACKUP_DIR" -name "*.db" -mtime +30 -delete
find "$BACKUP_DIR" -name "*.tar.gz" -mtime +30 -delete

echo "Backup complete: $BACKUP_DIR"
```

**Restore:**
```bash
pm2 stop dogar-portal
mv data/dogar_trading.db data/dogar_trading.db.old
cp /opt/backups/dogar-portal/dogar_trading_YYYY-MM-DD.db data/dogar_trading.db
pm2 start dogar-portal
```

---

## 8) Health Check

```bash
curl https://portal.example.com/api/ping
# → {"ok": true, "service": "dogar-portal", "version": "1.1.0"}
```

---

## 9) Security Notes

- `/api/docs` and `/api/redoc` are **disabled** when `ENV=production`. They are only
  reachable in development mode.
- All cookies use `httponly=True`, `samesite="lax"`, `secure=True`.
- Security response headers (CSP, HSTS, X-Frame-Options, Referrer-Policy) are injected
  on every response.
- The brute-force rate limiter locks out an IP after 8 failed login attempts within 10 minutes.
  The same limiter applies to `/forgot-password`.
- Password reset tokens expire after 30 minutes and are single-use.
- The vendor super-admin account requires `must_change_password` on first login.

---

## 10) Applying Schema Changes to Tenant Databases

The portal is **multi-tenant**. Each "Copy Portal" business partner runs on an
isolated SQLite database:

| Database | Path | Migrated by |
|----------|------|-------------|
| Main control DB | `data/dogar_trading.db` | `alembic upgrade head` (or the app's idempotent startup seeders) |
| Per-tenant DBs | `data/tenants/tenant_<slug>.db` | **`scripts/apply_tenant_migrations.py`** |

When you ship a schema change (a **new table** or a **new column** on an
existing table), the main DB is migrated by Alembic, but **the per-tenant DBs
are NOT touched by Alembic** — they are provisioned with
`Base.metadata.create_all()` in `app/services/tenant_service.py`. They must be
brought up to date separately, or requests routed to those tenants will error.

### 10.1 Automatic (Docker — recommended)

Nothing to do. `docker-entrypoint.sh` runs the roll-out on **every container
start**, in this order, before uvicorn accepts traffic:

```
1. alembic upgrade head                    # main control DB
2. python scripts/apply_tenant_migrations.py   # every tenant DB
3. exec uvicorn ...                         # start the API
```

So a normal redeploy applies all schema changes with zero extra steps:

```bash
docker compose build --no-cache
docker compose up -d
docker compose logs -f portal   # watch the "[entrypoint]" migration lines
```

### 10.2 Manual (bare-metal / PM2, or ad-hoc)

Run the same steps by hand after pulling new code:

```bash
cd dogar_trading_portal
source venv/bin/activate

# 1. Migrate the main control DB (if Alembic is configured):
alembic upgrade head        # (skip if this build has no alembic/ dir)

# 2. Roll the current schema out to every tenant DB:
python scripts/apply_tenant_migrations.py

# 3. Restart the server so it loads the new code:
pm2 restart dogar-portal
```

### 10.3 What `apply_tenant_migrations.py` does (and does not) do

The script is **idempotent and non-destructive** — safe to run on every deploy:

- ✅ **New tables** → created via `Base.metadata.create_all()` (no-op if present).
- ✅ **New columns** → detected by diffing each model against the live
  `PRAGMA table_info` and added with `ALTER TABLE … ADD COLUMN`. Existing rows
  are preserved; the column is added as nullable (or with a safe literal
  `DEFAULT`) so the `ALTER` never fails on a populated table.
- ❌ **Destructive changes** (dropping a column, renaming a column, changing a
  column type, adding a `NOT NULL` column without a default to a populated
  table) — SQLite's `ALTER TABLE` cannot express these. They still require a
  **manual, per-tenant migration** (typically: create a new table with the
  target shape, `INSERT … SELECT` the old data, drop the old table, rename).
  Do this for each `data/tenants/tenant_*.db` file, or write a one-off script.

Exit code is `0` on success and `1` if any tenant DB failed (the run continues
past a failing DB so one bad file does not block the rest — check the logs).

### 10.4 Verifying

```bash
# List tenant DBs:
ls -1 data/tenants/tenant_*.db

# Inspect a tenant's schema for a specific table/column:
sqlite3 data/tenants/tenant_<slug>.db "PRAGMA table_info(clients);"
```

---

© 2025–2026 · Built and maintained by **HussnainTechVertex Pvt Ltd.**
