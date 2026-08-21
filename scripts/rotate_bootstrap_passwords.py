#!/usr/bin/env python3
"""Rotate the bootstrap admin + vendor credentials and revoke existing sessions.

This is the project's credential-rotation mechanism. It reuses the app's own
user-management layer (``app.models.User`` + ``app.core.security.hash_password``)
rather than touching password hashes with raw SQL, so the stored hash format can
never drift from what ``verify_password()`` expects.

WHAT IT ROTATES
---------------
1. ``DEFAULT_ADMIN_PASSWORD`` -- the bootstrap admin account.
2. ``VENDOR_ADMIN_PASSWORD`` -- the vendor / developer super-admin account.
3. ``SECRET_KEY`` (unless ``--keep-secret-key``).

WHY SECRET_KEY IS PART OF A PASSWORD ROTATION
---------------------------------------------
This app's only browser session credential is the HttpOnly ``access_token``
cookie: a stateless JWT signed with ``SECRET_KEY`` (see
``app/core/security.py::create_access_token``). There is no server-side session
table, no refresh-token table and no JWT denylist, so changing a password does
**not** invalidate a token that was already issued -- an attacker holding a
stolen cookie keeps access until it expires.

Rotating ``SECRET_KEY`` is therefore the app's session-revocation lever: every
previously issued token fails signature verification immediately. That is the
correct response when a credential is believed compromised.

Outstanding ``password_reset_tokens`` rows are marked used at the same time, so
a reset link mailed before the incident cannot be redeemed afterwards.

SECRETS ARE NEVER PRINTED
-------------------------
An earlier version of this script printed the new passwords to stdout. Under
PM2 / Docker / CI, stdout is a log file, so that turned a rotation into a fresh
leak. The new values are now written straight into ``.env`` (gitignored) and
only a non-reversible SHA-256 fingerprint prefix is shown, so an operator can
confirm which value landed without the value itself ever entering a log.

Pass ``--print-admin-password`` only for an interactive, non-logged terminal
where the operator must read the credential out to set up a password manager.

USAGE
-----
    cd /home/user/webapp
    ./venv/bin/python scripts/rotate_bootstrap_passwords.py

    # rotate passwords but keep existing sessions valid (not recommended
    # after a compromise)
    ./venv/bin/python scripts/rotate_bootstrap_passwords.py --keep-secret-key

    # show what would change, touch nothing
    ./venv/bin/python scripts/rotate_bootstrap_passwords.py --dry-run

After running, restart the app so it picks up the new .env:
    pm2 restart dogar-portal
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import secrets
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.core.config import settings  # noqa: E402
from app.core.security import hash_password, verify_password  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models import User  # noqa: E402

ENV_PATH = _PROJECT_ROOT / ".env"

# 32 bytes -> 43 URL-safe characters. Comfortably above the 12-char minimum
# enforced by /api/auth/change-password and the production guardrails in
# app/core/config.py.
_PASSWORD_BYTES = 32


def _strong_password() -> str:
    return secrets.token_urlsafe(_PASSWORD_BYTES)


def _fingerprint(value: str) -> str:
    """Short, non-reversible identifier for a secret.

    Safe to print: 12 hex chars of SHA-256 cannot be brute-forced back to a
    43-char random password, but two operators can compare fingerprints to
    confirm they hold the same value.
    """
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def _set_env_var(text: str, key: str, value: str) -> str:
    """Replace (or append) ``KEY=value`` in a .env body, preserving comments."""
    pattern = re.compile(rf"^{re.escape(key)}\s*=.*$", re.MULTILINE)
    if pattern.search(text):
        return pattern.sub(f"{key}={value}", text, count=1)
    sep = "" if text.endswith("\n") or not text else "\n"
    return f"{text}{sep}{key}={value}\n"


def _reset_token_model():
    """Return the PasswordResetToken model, or None if unavailable."""
    try:
        from app.models.lookups import PasswordResetToken
        return PasswordResetToken
    except Exception:
        return None


def _table_exists(db, model) -> bool:
    from sqlalchemy import inspect as sa_inspect
    try:
        return model.__tablename__ in set(
            sa_inspect(db.get_bind()).get_table_names()
        )
    except Exception:
        return False


def _count_pending_reset_tokens(db):
    """Number of unused reset tokens, or None when the table is absent."""
    model = _reset_token_model()
    if model is None or not _table_exists(db, model):
        return None
    return db.query(model).filter(model.used_at.is_(None)).count()


def _revoke_reset_tokens(db) -> int:
    """Mark every unused password-reset token as consumed.

    Returns the number of rows revoked. Tolerates the ``password_reset_tokens``
    table not existing (fresh install before migrations have run) without
    aborting the rotation.

    DO NOT PUT ``db.rollback()`` IN HERE
    ------------------------------------
    The first version of this helper wrapped the query in
    ``except Exception: db.rollback(); return 0``. On any database where the
    table was absent, that rollback discarded the *pending password change* made
    by the caller a few lines earlier -- so ``rotate()`` reported
    "verification FAILED" and left the compromised password in place. A
    rotation that silently does nothing is worse than no rotation at all,
    because the operator believes they are safe.

    ``tests/test_no_committed_secrets.py::
    test_rotation_actually_changes_the_stored_password_hash`` runs the real
    rotate() against a throwaway DB that has only the ``users`` table, which is
    exactly this condition. It caught the bug.

    The table's presence is now probed with the inspector *before* any query, so
    the session's transaction is never poisoned and never needs rolling back.
    """
    from datetime import datetime

    model = _reset_token_model()
    if model is None or not _table_exists(db, model):
        return 0

    rows = db.query(model).filter(model.used_at.is_(None)).all()
    now = datetime.utcnow()
    for row in rows:
        row.used_at = now
    return len(rows)


def rotate(*, keep_secret_key: bool = False, dry_run: bool = False,
           print_admin_password: bool = False) -> int:
    admin_email = (settings.DEFAULT_ADMIN_EMAIL or "").strip()
    vendor_email = (settings.VENDOR_ADMIN_EMAIL or "").strip()

    if not admin_email:
        print("ERROR: DEFAULT_ADMIN_EMAIL is empty in .env.", file=sys.stderr)
        return 2

    new_admin_pw = _strong_password()
    new_vendor_pw = _strong_password()
    new_secret_key = secrets.token_hex(32)  # 64 hex chars, matches existing format

    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.email == admin_email).first()
        if not admin:
            print(f"ERROR: admin user not found: {admin_email}", file=sys.stderr)
            print("       Run scripts/reset_admin_password.py --list to inspect.",
                  file=sys.stderr)
            return 3
        vendor = (
            db.query(User).filter(User.email == vendor_email).first()
            if vendor_email else None
        )

        if dry_run:
            print("DRY RUN -- no changes written.")
            print(f"  would rotate admin  : {admin_email} (id={admin.id})")
            if vendor:
                print(f"  would rotate vendor : {vendor_email} (id={vendor.id})")
            elif vendor_email:
                print(f"  vendor user absent  : {vendor_email} (skipped)")
            print(f"  would rotate SECRET_KEY: {not keep_secret_key}")
            # Counted through the same table-presence probe the real path uses,
            # so --dry-run cannot crash on a DB without the tokens table.
            pending = _count_pending_reset_tokens(db)
            print(f"  outstanding password-reset tokens: "
                  f"{'unknown (table absent)' if pending is None else pending}")
            return 0

        admin.password_hash = hash_password(new_admin_pw)
        admin.must_change_password = False
        admin.is_active = True
        if vendor:
            vendor.password_hash = hash_password(new_vendor_pw)
            vendor.must_change_password = False
            vendor.is_active = True

        revoked = _revoke_reset_tokens(db)
        db.commit()

        # Never claim success on a hash that cannot actually verify.
        db.refresh(admin)
        if not verify_password(new_admin_pw, admin.password_hash):
            print("ERROR: admin password written but verification FAILED.",
                  file=sys.stderr)
            return 4
        if vendor:
            db.refresh(vendor)
            if not verify_password(new_vendor_pw, vendor.password_hash):
                print("ERROR: vendor password written but verification FAILED.",
                      file=sys.stderr)
                return 4
    finally:
        db.close()

    # --- persist to .env (gitignored) -------------------------------------
    if ENV_PATH.exists():
        body = ENV_PATH.read_text()
    else:
        body = ""
    body = _set_env_var(body, "DEFAULT_ADMIN_PASSWORD", new_admin_pw)
    if vendor_email:
        body = _set_env_var(body, "VENDOR_ADMIN_PASSWORD", new_vendor_pw)
    if not keep_secret_key:
        body = _set_env_var(body, "SECRET_KEY", new_secret_key)
    ENV_PATH.write_text(body)
    os.chmod(ENV_PATH, 0o600)

    print("=" * 68)
    print("CREDENTIALS ROTATED")
    print("=" * 68)
    print(f"  admin account      : {admin_email}")
    print(f"  admin pw fingerprint: sha256:{_fingerprint(new_admin_pw)}  "
          f"({len(new_admin_pw)} chars)")
    if vendor_email:
        print(f"  vendor account     : {vendor_email}")
        print(f"  vendor pw fingerprint: sha256:{_fingerprint(new_vendor_pw)}  "
              f"({len(new_vendor_pw)} chars)")
    if keep_secret_key:
        print("  SECRET_KEY         : UNCHANGED (--keep-secret-key); "
              "previously issued session cookies REMAIN VALID")
    else:
        print(f"  SECRET_KEY         : rotated (sha256:{_fingerprint(new_secret_key)})"
              " -- all existing access_token cookies are now invalid")
    print(f"  password-reset tokens revoked: {revoked}")
    print("-" * 68)
    print(f"  written to {ENV_PATH} (mode 0600, gitignored)")
    print("  The secret VALUES are deliberately not printed. Read them from")
    print("  .env on the host, or re-run with --print-admin-password on an")
    print("  interactive terminal that is not being logged.")
    print("  Restart the app to load the new .env:  pm2 restart dogar-portal")
    print("=" * 68)

    if print_admin_password:
        print()
        print("!! The next line is a live credential. Do not paste it anywhere !!")
        print(f"DEFAULT_ADMIN_PASSWORD={new_admin_pw}")

    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--keep-secret-key", action="store_true",
                    help="do not rotate SECRET_KEY (existing sessions stay valid)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change and exit")
    ap.add_argument("--print-admin-password", action="store_true",
                    help="print the new admin password (interactive terminals only)")
    args = ap.parse_args()
    return rotate(
        keep_secret_key=args.keep_secret_key,
        dry_run=args.dry_run,
        print_admin_password=args.print_admin_password,
    )


if __name__ == "__main__":
    raise SystemExit(main())
