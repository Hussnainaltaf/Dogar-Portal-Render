#!/usr/bin/env python3
"""Reset (or set) an admin/user password from the command line.

This is the operator-facing recovery tool referenced in the audit and the
README. It is intentionally standalone so it can be run even when the web app
cannot be logged into (e.g. after a forgotten / drifted admin password).

WHY THIS EXISTS
---------------
`app/db/init_db.py::seed_admin_user()` only sets a password the *first* time an
admin row is created. If `DEFAULT_ADMIN_PASSWORD` in `.env` is later changed,
the existing DB row keeps its old hash and login silently fails with
"Invalid username or password". This script re-hashes and writes the new
password onto the existing row so `.env` and the DB agree again.

USAGE
-----
    # Reset the .env DEFAULT_ADMIN_EMAIL user to the .env DEFAULT_ADMIN_PASSWORD
    python scripts/reset_admin_password.py

    # Reset a specific user to an explicit password
    python scripts/reset_admin_password.py --email admin@dogartrading.com --password 'NewStrongPass123!'

    # Also clear the "must change password on next login" flag
    python scripts/reset_admin_password.py --email admin@dogartrading.com --password 'NewStrongPass123!' --clear-must-change

    # List existing users (no changes made)
    python scripts/reset_admin_password.py --list

Run from the project root with the virtualenv active:
    source venv/bin/activate && python scripts/reset_admin_password.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure the project root is importable when invoked as `python scripts/...`.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.core.config import settings  # noqa: E402
from app.core.security import hash_password, verify_password  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.user import User  # noqa: E402


def _list_users(db) -> None:
    users = db.query(User).order_by(User.id).all()
    if not users:
        print("No users found in the database.")
        return
    print(f"{'ID':<4} {'EMAIL':<34} {'ROLE':<8} {'SUPER':<6} {'ACTIVE':<7} MUST_CHANGE")
    print("-" * 78)
    for u in users:
        print(
            f"{u.id:<4} {(u.email or ''):<34} {(u.role or ''):<8} "
            f"{str(bool(u.is_super_admin)):<6} {str(bool(u.is_active)):<7} "
            f"{bool(getattr(u, 'must_change_password', False))}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reset or set an admin/user password."
    )
    parser.add_argument(
        "--email",
        default=None,
        help="Email of the user to reset. Defaults to DEFAULT_ADMIN_EMAIL from .env.",
    )
    parser.add_argument(
        "--password",
        default=None,
        help="New password. Defaults to DEFAULT_ADMIN_PASSWORD from .env.",
    )
    parser.add_argument(
        "--clear-must-change",
        action="store_true",
        help="Clear the must_change_password flag so the user is not forced to "
             "change the password at next login.",
    )
    parser.add_argument(
        "--activate",
        action="store_true",
        help="Ensure the account is active (is_active=True).",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List existing users and exit without making changes.",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.list:
            _list_users(db)
            return 0

        email = (args.email or settings.DEFAULT_ADMIN_EMAIL or "").strip()
        password = args.password if args.password is not None else settings.DEFAULT_ADMIN_PASSWORD

        if not email:
            print("ERROR: No email given and DEFAULT_ADMIN_EMAIL is empty.", file=sys.stderr)
            return 2
        if not password:
            print("ERROR: No password given and DEFAULT_ADMIN_PASSWORD is empty.", file=sys.stderr)
            return 2
        if len(password) < 8:
            print(
                "ERROR: Refusing to set a password shorter than 8 characters.",
                file=sys.stderr,
            )
            return 2

        user = db.query(User).filter(User.email == email).first()
        if not user:
            print(f"ERROR: No user found with email {email!r}.", file=sys.stderr)
            print("Run with --list to see existing users.", file=sys.stderr)
            return 3

        user.password_hash = hash_password(password)
        if args.clear_must_change:
            user.must_change_password = False
        if args.activate:
            user.is_active = True
        db.commit()

        # Verify the round-trip so we never claim success on a broken hash.
        db.refresh(user)
        ok = verify_password(password, user.password_hash)
        if not ok:
            print("ERROR: Password was written but verification FAILED.", file=sys.stderr)
            return 4

        print(f"OK: Password reset for {email} (id={user.id}).")
        print(f"    role={user.role} super_admin={bool(user.is_super_admin)} "
              f"active={bool(user.is_active)} "
              f"must_change_password={bool(getattr(user, 'must_change_password', False))}")
        print("    Verification: login with this password will succeed.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
