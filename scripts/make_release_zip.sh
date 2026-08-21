#!/usr/bin/env bash
# ============================================================================
# make_release_zip.sh — build the distributable project archive.
#
# Packaging policy (learned the hard way — 2026-07-17 audit):
#   * NEVER ship secrets:        .env, .env.*, *.bak      (only .env.example)
#   * NEVER ship crash artifacts: core, core.*, *.core
#   * NEVER ship caches:         .ruff_cache, .pytest_cache, __pycache__,
#                                node_modules, venv, .wrangler, .pm2, logs
#   * NEVER ship git history:    .git
#   * NEVER ship live data:      data/ (real records + tenant DBs are the
#                                operator's private property — delivered
#                                separately, never inside the distributable)
#
# Usage:  bash scripts/make_release_zip.sh [output.zip]
# ============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."
OUT="${1:-/tmp/hussnaintechvertex_portal_$(date +%F).zip}"

rm -f "$OUT"

zip -r -q "$OUT" . \
  -x ".git/*" \
  -x ".env" -x ".env.*" -x "*.env.local" \
  -x "core" -x "core.*" -x "*.core" \
  -x ".ruff_cache/*" -x "*/.ruff_cache/*" \
  -x ".pytest_cache/*" -x "*/.pytest_cache/*" \
  -x ".mypy_cache/*" -x "*/.mypy_cache/*" \
  -x "*__pycache__*" -x "*.pyc" -x "*.pyo" \
  -x "node_modules/*" -x "*/node_modules/*" \
  -x "app/tailwind_build/node_modules/*" \
  -x "venv/*" -x ".venv/*" \
  -x ".wrangler/*" -x ".pm2/*" \
  -x "logs/*" -x "*.log" \
  -x "data/*" -x "data/*/*" -x "data/**" \
  -x "package-lock.json" -x "*/package-lock.json" \
  -x "*.bak" -x "*.pre_hig_bak" -x "*.backup" -x "*.tar.gz" -x "*.tar" -x "*.zip" \
  -x "app/frontend/tsconfig.tsbuildinfo" \
  -x "app/frontend/playwright-report/*" -x "app/frontend/test-results/*" \
  -x ".DS_Store" -x "*/.DS_Store" -x "Thumbs.db"

# .env.example must be present (it is the config template the operator copies).
zip -q "$OUT" .env.example

# ── Self-audit: fail loudly if anything forbidden slipped in ──
# Every pattern below caused (or could cause) a real leak in a prior release.
LISTING=$(unzip -l "$OUT")
FAIL=0
for pat in '\.env$' '\.env\.production' '\.env\.postgres' '\.env\.sqlite' \
           '\.env\.local' '^ *[0-9]+ .* core$' \
           '\.ruff_cache' '\.pytest_cache' '__pycache__' \
           'node_modules/' 'tailwind_build/node_modules' \
           '\.git/' \
           'data/dogar_trading\.db' 'data/tenants/' 'data/backups/' \
           '\.db$' '\.db-wal$' '\.db-shm$' \
           'portal-backup-.*\.zip'; do
  if echo "$LISTING" | grep -qE "$pat"; then
    echo "PACKAGING VIOLATION: pattern '$pat' found in archive" >&2
    FAIL=1
  fi
done
if ! echo "$LISTING" | grep -q '\.env\.example'; then
  echo "PACKAGING VIOLATION: .env.example missing from archive" >&2
  FAIL=1
fi
[ "$FAIL" -eq 1 ] && { rm -f "$OUT"; exit 1; }

echo "OK: $(du -h "$OUT" | cut -f1) $(echo "$LISTING" | tail -1 | awk '{print $2}') files → $OUT"
