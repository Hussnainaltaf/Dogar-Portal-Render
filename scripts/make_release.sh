#!/usr/bin/env bash
# ============================================================================
# make_release.sh — build the deploy-ready distributable WITH FRESH GIT HISTORY.
#
# Difference from make_release_zip.sh:
#   make_release_zip.sh ships NO .git at all (a bare source drop).
#   This script ships a NEW repository whose history is exactly ONE commit,
#   so the customer gets version control from day one without inheriting the
#   internal development history.
#
# Why `git archive` instead of `cp` + excludes:
#   git archive exports ONLY tracked files. Secrets (.env), live databases
#   (data/), virtualenvs, caches and logs are untracked/ignored, so they
#   CANNOT leak by construction — we are not relying on an exclude list
#   staying in sync with reality.
#
# Usage:  bash scripts/make_release.sh [output.zip]
# ============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."
SRC="$(pwd)"
OUT="${1:-/tmp/dogar-portal-v1.0.0.zip}"
STAGE="$(mktemp -d)"
TREE="$STAGE/dogar-portal"
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$TREE"

# ── 1. Export tracked files only ────────────────────────────────────────────
git -C "$SRC" archive --format=tar HEAD | tar -x -C "$TREE"

cd "$TREE"

# ── 2. Tidy the root: internal dev-history reports -> docs/project-history ──
# A shipped product should not greet the operator with nine dated internal
# audit/fix reports at top level. They stay in the package (useful provenance)
# but move out of the way. Links that point at them are rewritten below.
mkdir -p docs/project-history
HISTORY_DOCS=(
  AUDIT_REPORT.md
  AUDIT_REPORT_2026-07-06.md
  AUDIT_REPORT_2026-07-08.md
  ENHANCEMENTS_2026-07-10.md
  ENHANCEMENTS_2026-07-11.md
  FIXES_APPLIED_2026-07-07.md
  FIXES_APPLIED_2026-07-10.md
  PRODUCTION_READINESS_AUDIT_2026-07-12.md
)
for f in "${HISTORY_DOCS[@]}"; do
  [ -f "$f" ] && mv "$f" docs/project-history/
done

# Rewrite the references to the moved files (verified set — see grep below).
sed -i \
  -e 's|(ENHANCEMENTS_2026-07-11\.md)|(docs/project-history/ENHANCEMENTS_2026-07-11.md)|g' \
  -e 's|(ENHANCEMENTS_2026-07-10\.md)|(docs/project-history/ENHANCEMENTS_2026-07-10.md)|g' \
  -e 's|(FIXES_APPLIED_2026-07-10\.md)|(docs/project-history/FIXES_APPLIED_2026-07-10.md)|g' \
  README.md
sed -i "s|see AUDIT_REPORT.md 'Custom-domain|see docs/project-history/AUDIT_REPORT.md 'Custom-domain|" \
  app/api/endpoints/tenants.py

# Fail if any markdown link in a shipped top-level doc now dangles.
BROKEN=0
for src in README.md PROJECT_DOCUMENTATION.md DEPLOY.md AUDIT_RESPONSE_2026-07-26.md; do
  [ -f "$src" ] || continue
  while read -r t; do
    [ -z "$t" ] && continue
    [ -e "$t" ] || { echo "BROKEN LINK: $src -> $t" >&2; BROKEN=1; }
  done < <(grep -oh -E '\]\([A-Za-z0-9_./-]+\.md\)' "$src" | sed 's/^](//;s/)$//')
done
[ "$BROKEN" -eq 1 ] && exit 1

# Structural sanity of the shipped Markdown (2026-07-28 incident).
# An unclosed <details> collapses EVERY following section when rendered, so a
# customer opening PROJECT_DOCUMENTATION.md on GitHub would have seen sections
# 7.4-18 vanish. Plain text and `git diff` both look fine, which is exactly why
# this needs a machine check. Same for an unterminated ``` fence.
# tests/test_docs_integrity.py enforces this in CI; this is the belt-and-braces
# copy that runs even when the release is cut without a test run.
MDBAD=0
for src in README.md PROJECT_DOCUMENTATION.md DEPLOY.md \
           docs/CSP_HARDENING_OPTION_B.md docs/PERFORMANCE_REPORT.md \
           docs/SECURITY_PENTEST_REPORT.md; do
  [ -f "$src" ] || continue
  for tag in details summary; do
    # NOTE: `|| true` is REQUIRED. grep exits 1 when a pattern matches nothing,
    # and this script runs under `set -e`, so a document that legitimately has
    # no <details> at all would abort the whole release build. That exact bug
    # was introduced with this check and caught only because the build went
    # silent instead of printing its OK line.
    o=$(grep -o -E "<$tag(\s[^>]*)?>" "$src" 2>/dev/null | wc -l || true)
    c=$(grep -o -E "</$tag\s*>" "$src" 2>/dev/null | wc -l || true)
    if [ "$o" -ne "$c" ]; then
      echo "DOC VIOLATION: $src has unbalanced <$tag> ($o open vs $c close)" >&2
      MDBAD=1
    fi
  done
  f=$(grep -c -E '^\s*```' "$src" || true)
  if [ $((f % 2)) -ne 0 ]; then
    echo "DOC VIOLATION: $src has $f code-fence markers (odd) — a fence is unclosed" >&2
    MDBAD=1
  fi
done
# The main documentation advertises 18 numbered sections; refuse to ship fewer.
if [ -f PROJECT_DOCUMENTATION.md ]; then
  SECS=$(grep -c -E '^##[[:space:]]+[0-9]+\.[[:space:]]' PROJECT_DOCUMENTATION.md || true)
  if [ "$SECS" -lt 18 ]; then
    echo "DOC VIOLATION: PROJECT_DOCUMENTATION.md has only $SECS numbered sections (expected >= 18)" >&2
    MDBAD=1
  fi
fi
[ "$MDBAD" -eq 1 ] && exit 1

# ── 3. Fresh git history: exactly one commit ────────────────────────────────
git init -q -b main
git config user.email "release@hussnaintechvertex.com"
git config user.name "Hussnain TechVertex Release"
git add -A
# package-lock.json is matched by .gitignore but is intentionally tracked
# upstream: a release must pin its frontend dependency graph.
[ -f app/frontend/package-lock.json ] && git add -f app/frontend/package-lock.json
git commit -q -F "$SRC/scripts/release_commit_message.txt"

if [ "$(git rev-list --count HEAD)" -ne 1 ]; then
  echo "RELEASE VIOLATION: history is not a single commit" >&2
  exit 1
fi

# AUDIT 2026-07-29 (Low): "the ZIP ships with .git/ ... bloats the release
# artifact". The .git IS intentional -- the brief asked for a package with fresh
# history, and a one-commit repo is how the customer gets a working starting
# point (git log, git diff, their own first branch) instead of an inert file
# drop. What was NOT intentional was its size: `git add` leaves every blob as a
# separate zlib file, so the archive carried 378 LOOSE OBJECTS -- 612 of the
# ZIP's 995 entries and ~7 MB -- purely because gc had never run.
#
# `git gc` repacks those 378 files into a single pack; the sample hooks git
# copies into every new repo are dead weight in a distributed artifact. Together
# this takes .git from 612 files to 15 with the history fully intact, which
# answers the size objection without taking the feature away.
git gc --quiet --aggressive --prune=now
rm -rf .git/hooks

# The repo must still be valid and still be exactly one commit after packing.
git fsck --no-progress --no-dangling >/dev/null 2>&1 || {
  echo "RELEASE VIOLATION: git fsck failed after gc" >&2; exit 1; }
if [ "$(git rev-list --count HEAD)" -ne 1 ]; then
  echo "RELEASE VIOLATION: history is not a single commit after gc" >&2
  exit 1
fi
if [ -n "$(git status --porcelain)" ]; then
  echo "RELEASE VIOLATION: working tree is dirty after gc" >&2
  git status --porcelain >&2
  exit 1
fi

# ── 4. Self-audit: nothing forbidden may be present ────────────────────────
FAIL=0
while read -r bad; do
  [ -n "$bad" ] && { echo "RELEASE VIOLATION: forbidden path '$bad'" >&2; FAIL=1; }
done < <(find . -path ./.git -prune -o \
  \( -name '.env' -o -name '.env.production' -o -name '.env.local' \
     -o -name '*.db' -o -name '*.db-wal' -o -name '*.db-shm' \
     -o -name '*.log' -o -name '*.bak' -o -name '*.pre_hig_bak' \
     -o -name '__pycache__' -o -name '.pytest_cache' -o -name '.ruff_cache' \
     -o -name 'node_modules' -o -name 'venv' -o -name '.venv' \) -print)

for d in data logs; do
  [ -e "$d" ] && { echo "RELEASE VIOLATION: '$d/' present" >&2; FAIL=1; }
done
[ -f .env.example ] || { echo "RELEASE VIOLATION: .env.example missing" >&2; FAIL=1; }

# Packaging-hygiene gates (2026-07-29 audit). These assert the OUTCOME rather
# than trusting that the gc step above ran, so a future edit that reorders or
# removes it fails the build instead of quietly shipping a 7 MB .git again.
# NOTE the `-path '*/info/*'` exclusion. A loose object lives at
# .git/objects/<2-hex>/<38-hex>, but `git gc` also WRITES .git/objects/info/
# {packs,commit-graph} -- its own metadata, not objects. The first version of
# this gate counted those two files and failed a perfectly-packed repo, so the
# pattern now matches only real object directories.
LOOSE=$(find .git/objects -type f -not -path '*/pack/*' -not -path '*/info/*' 2>/dev/null | wc -l)
if [ "$LOOSE" -gt 0 ]; then
  echo "RELEASE VIOLATION: $LOOSE loose git objects survived gc (expected 0)" >&2
  find .git/objects -type f -not -path '*/pack/*' -not -path '*/info/*' >&2
  FAIL=1
fi
GITFILES=$(find .git -type f 2>/dev/null | wc -l)
if [ "$GITFILES" -gt 40 ]; then
  echo "RELEASE VIOLATION: .git holds $GITFILES files (expected <= 40 after gc)" >&2
  FAIL=1
fi
# The auditor reported 12 __pycache__ directories. None of my builds contain any
# -- `git archive` exports only tracked files and __pycache__ is gitignored, so
# they were almost certainly created by the auditor's own py_compile/import
# analysis inside their extracted copy. This gate is kept anyway: it costs
# nothing and makes the claim falsifiable on every future build.
PYC=$(find . -path ./.git -prune -o \( -name '__pycache__' -o -name '*.pyc' \) -print 2>/dev/null | wc -l)
if [ "$PYC" -gt 0 ]; then
  echo "RELEASE VIOLATION: $PYC __pycache__/*.pyc entries in the package" >&2
  FAIL=1
fi

# No real vendor credential may survive ANYWHERE in the package — not in
# shipping code, not in .env.example prose, not in the archived history
# reports. The patterns are split so this guard does not match itself.
VENDOR_MAIL='hussnainmr07''@gmail\.com'
VENDOR_PASS='Romio''@786'
if grep -rnE "$VENDOR_MAIL|$VENDOR_PASS" . --exclude-dir=.git \
     --exclude=make_release.sh 2>/dev/null | grep -q .; then
  echo "RELEASE VIOLATION: real vendor credential found in package:" >&2
  grep -rnE "$VENDOR_MAIL|$VENDOR_PASS" . --exclude-dir=.git \
    --exclude=make_release.sh 2>/dev/null | head -10 >&2
  FAIL=1
fi
[ "$FAIL" -eq 1 ] && exit 1

# ── 5. Package ─────────────────────────────────────────────────────────────
rm -f "$OUT"
cd "$STAGE"
zip -r -q "$OUT" dogar-portal -x '*.DS_Store' -x '*Thumbs.db'

echo "OK  $(du -h "$OUT" | cut -f1)  $(unzip -l "$OUT" | tail -1 | awk '{print $2}') entries  -> $OUT"
