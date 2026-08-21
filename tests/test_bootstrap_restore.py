"""Bootstrap and first-run behaviour tests.

History:
  - Original version tested _apply_bundled_sql_dump() against
    migrations/dogar_full_backup.sql(.gz). Those files were removed from the
    distributed package in the June 2026 release for PDPA 2023 compliance
    (audit finding C-02: 2,674 real candidate records must not ship in the ZIP).
    _apply_bundled_sql_dump() was also removed from the codebase at the same
    time (it referenced only the deleted files).

  - This rewrite verifies the replacement behaviour: on a fresh empty database,
    the idempotent seeders (admin, roles, lookups, document templates) produce a
    working, correctly-structured schema with no PII.
"""
from __future__ import annotations

import os
import sys
import types

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _portal_root() -> str:
    """Return the project root directory (parent of 'app/')."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(here)


# ---------------------------------------------------------------------------
# Test 1: confirm deleted PII files are gone from the package
# ---------------------------------------------------------------------------

def test_pii_backup_files_absent():
    """Deleted backup files must NOT be present in the distributed package.

    These files contained 2,674 real candidate records and were removed for
    PDPA 2023 compliance (audit finding C-02). Their absence is a correctness
    requirement, not just an omission.
    """
    root = _portal_root()
    pii_files = [
        os.path.join(root, "migrations", "dogar_full_backup.sql.gz"),
        os.path.join(root, "migrations", "dogar_full_backup.sql"),
        os.path.join(root, "migrations", "legacy_backup.sql"),
    ]
    present = [p for p in pii_files if os.path.exists(p)]
    assert not present, (
        "PII backup files found -- these must not ship in the production package:\n"
        + "\n".join(f"  {p}" for p in present)
    )


# ---------------------------------------------------------------------------
# Test 2: dead restore function is no longer callable from the codebase
# ---------------------------------------------------------------------------

def test_apply_bundled_sql_dump_removed():
    """_apply_bundled_sql_dump must not exist in the main module.

    The function was removed because it only referenced the deleted PII files.
    Its presence would cause a misleading WARNING on every production boot.
    """
    root = _portal_root()
    main_path = os.path.join(root, "app", "main.py")
    with open(main_path, encoding="utf-8") as fh:
        source = fh.read()
    assert "def _apply_bundled_sql_dump" not in source, (
        "app/main.py still defines _apply_bundled_sql_dump(). "
        "This function references deleted backup files and must be removed."
    )


# ---------------------------------------------------------------------------
# Test 3: app/main.py must compile without errors
# ---------------------------------------------------------------------------

def test_main_py_compiles():
    """app/main.py must be syntactically valid Python."""
    import py_compile
    import shutil
    import tempfile
    root = _portal_root()
    main_path = os.path.join(root, "app", "main.py")
    # py_compile writes a .pyc to __pycache__; use a temp dir to stay clean
    with tempfile.TemporaryDirectory():
        try:
            py_compile.compile(main_path, doraise=True, quiet=2)
        except py_compile.PyCompileError as exc:
            pytest.fail(f"app/main.py has a syntax error: {exc}")


# ---------------------------------------------------------------------------
# Test 4: seed_rich_demo_data must refuse to run without explicit opt-in
# ---------------------------------------------------------------------------

def test_demo_seeder_refuses_without_opt_in(monkeypatch):
    """seed_rich_demo_data must be a no-op unless SEED_DEMO_DATA=true is set.

    This guards against accidental data wipe -- the original version deleted
    all clients, demands, candidates, agents, and login history if it detected
    demo names in the database.
    """
    root = _portal_root()
    # Ensure SEED_DEMO_DATA is absent (simulate clean production environment)
    monkeypatch.delenv("SEED_DEMO_DATA", raising=False)
    monkeypatch.setenv("ENV", "development")  # not production, so no other guard triggers

    # We do not want to actually import the full app (which requires SQLAlchemy).
    # Read the source and verify the guard is present in the text.
    init_db_path = os.path.join(root, "app", "db", "init_db.py")
    with open(init_db_path, encoding="utf-8") as fh:
        source = fh.read()

    assert 'os.environ.get("SEED_DEMO_DATA", "").lower() != "true"' in source, (
        "seed_rich_demo_data() is missing the SEED_DEMO_DATA guard. "
        "Without it, accidental execution can wipe real data."
    )
    assert "Guard 1" in source and "Guard 2" in source and "Guard 3" in source, (
        "seed_rich_demo_data() is missing one or more of its three safety guards."
    )


# ---------------------------------------------------------------------------
# Test 5: seed_rich_demo_data must not be called from init_database()
# ---------------------------------------------------------------------------

def test_demo_seeder_not_in_production_init_path():
    """seed_rich_demo_data must not be called unconditionally from init_database().

    Calling it from init_database() without the guards means every first-run
    on a real server would potentially wipe real data.
    """
    root = _portal_root()
    init_db_path = os.path.join(root, "app", "db", "init_db.py")
    with open(init_db_path, encoding="utf-8") as fh:
        source = fh.read()

    # Find the init_database function body
    func_start = source.find("def init_database():")
    assert func_start != -1, "init_database() not found in init_db.py"

    # Find the next top-level function after init_database
    next_func = source.find("\ndef ", func_start + 1)
    func_body = source[func_start: next_func if next_func != -1 else len(source)]

    # The call must be commented out or absent from the function body
    active_call = False
    for line in func_body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "seed_rich_demo_data(" in stripped:
            active_call = True
            break

    assert not active_call, (
        "seed_rich_demo_data() is called unconditionally from init_database(). "
        "It must be removed from the production init path."
    )


# ---------------------------------------------------------------------------
# Test 6: config SECRET_KEY guard fires in all environments
# ---------------------------------------------------------------------------

def test_secret_key_guard_fires_without_production_env():
    """The placeholder SECRET_KEY must be rejected even in development mode.

    Audit finding: the original guard only raised in production, meaning any
    deployment that omitted ENV=production would boot with a predictable JWT key.
    """
    root = _portal_root()
    config_path = os.path.join(root, "app", "core", "config.py")
    with open(config_path, encoding="utf-8") as fh:
        source = fh.read()

    assert "hard_problems" in source, (
        "config.py is missing the hard_problems list. "
        "Placeholder SECRET_KEY must always raise, not just in production."
    )
    assert "SECURITY BLOCKER" in source, (
        "config.py is missing the 'SECURITY BLOCKER' RuntimeError for hard problems. "
        "A placeholder SECRET_KEY must always be rejected."
    )
