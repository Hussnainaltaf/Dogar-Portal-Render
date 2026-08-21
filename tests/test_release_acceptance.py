"""
Acceptance tests for defects that only a *clean install* can expose.

WHY THIS FILE EXISTS
--------------------
On 2026-07-29 the release ZIP was installed from scratch (fresh venv, virgin
database, `alembic upgrade head`) and driven over HTTP the way an operator
would. Two production bugs surfaced immediately that the then-275-test suite
did not catch, both for the same underlying reason: **the existing tests run
against a schema built by `Base.metadata.create_all()`, while a real customer
gets a schema built by the Alembic migrations.** Those two schemas had drifted
apart, so an entire class of defect was invisible.

  1. `POST /api/demands/` returned 500 for the documented payload.
     `create_demand` builds the row as
         Demand(file_number=..., demand_code=file_number, **data)
     but `demand_code` is a *required* field of the public `DemandCreate`
     schema, so it is also present in `data`, and Python raises
     "got multiple values for keyword argument 'demand_code'". Any client
     sending the documented request body got a 500 -- creating a demand file
     is the core workflow of this product.

  2. The tenant resolver logged `no such column: tenants.license_number` on
     every single request. The `Tenant` model declares `license_number` and
     `office_address`, but the baseline migration never creates them, so on a
     migration-built database every tenant lookup raised. The dev database had
     the columns (added by `create_all()` at some point), which is precisely
     why nothing failed in CI.

These tests reproduce both against a **migration-built** database so the drift
cannot come back.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def migrated_db(tmp_path_factory) -> Path:
    """A database built the way a customer's is: by `alembic upgrade head`.

    The rest of the suite uses `Base.metadata.create_all()`, which derives the
    schema from the models and therefore can never detect a migration that has
    fallen behind those models. This fixture is the missing half.
    """
    workdir = tmp_path_factory.mktemp("migrated")
    db_path = workdir / "acceptance.db"
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "DATABASE_URL": f"sqlite:///{db_path}",
        "SECRET_KEY": "acceptance-only-key-not-a-real-secret-0123456789",
        "ENV": "development",
        "HOME": str(workdir),
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, (
        f"alembic upgrade head failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert db_path.exists(), "migrations did not produce a database file"
    return db_path


def _columns(db_path: Path, table: str) -> set[str]:
    con = sqlite3.connect(db_path)
    try:
        return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    finally:
        con.close()


def test_migrations_create_every_column_the_models_declare(migrated_db):
    """The migration-built schema must match the model-derived schema.

    This is the guard that would have caught bug 2 on the day it was
    introduced. It compares *every* table, not just `tenants`, because the
    same drift can happen anywhere.
    """
    import app.models  # noqa: F401  (registers all models on the metadata)
    from app.db.session import Base

    con = sqlite3.connect(migrated_db)
    try:
        actual_tables = {
            r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        con.close()

    missing: list[str] = []
    for name, table in sorted(Base.metadata.tables.items()):
        if name not in actual_tables:
            missing.append(f"{name}: TABLE MISSING from the migrations")
            continue
        declared = {c.name for c in table.columns}
        built = _columns(migrated_db, name)
        for column in sorted(declared - built):
            missing.append(f"{name}.{column}")

    assert not missing, (
        "the models declare columns that `alembic upgrade head` does not "
        "create, so a customer installing from the migrations gets a broken "
        "schema (this is invisible to tests that use create_all()):\n  "
        + "\n  ".join(missing)
    )


def test_create_demand_accepts_the_documented_payload(client, auth_headers):
    """`POST /api/demands/` must accept the body its own schema documents.

    `DemandCreate` marks `demand_code` required, so the documented request body
    contains it -- and that is exactly the body that used to return 500,
    because the handler also passed `demand_code` positionally when building the
    row. Creating a demand file is the central workflow of this product, so this
    was a total blocker for any API client following the schema.
    """
    r = client.post("/api/clients/",
                    json={"company_name": "Acceptance Employer LLC",
                          "country": "Saudi Arabia"},
                    headers=auth_headers)
    assert r.status_code == 200, r.text
    client_id = r.json()["id"]

    payload = {
        "demand_code": "ACC-001",
        "client_id": client_id,
        "visa_quota": 5,
        "status": "processing",
        "benefits": "Food and accommodation",
        "notes": "created by acceptance test",
    }
    r = client.post("/api/demands/", json=payload, headers=auth_headers)
    assert r.status_code == 200, (
        f"the documented DemandCreate payload was rejected: "
        f"{r.status_code} {r.text}"
    )
    body = r.json()
    assert body["client_id"] == client_id
    assert body["visa_quota"] == 5
    # demand_code is derived from the generated file_number, not taken from the
    # request -- assert that contract explicitly so it stays intentional.
    assert body.get("file_number"), "a file_number should have been generated"
    assert body["demand_code"] == body["file_number"], (
        "demand_code is documented as mirroring the generated file_number"
    )


def test_tenants_table_has_the_letterhead_branding_columns(migrated_db):
    """Explicit regression test for the exact columns that were missing.

    Kept alongside the general check above so that a failure names the real
    symptom an operator saw: every request logging
    `no such column: tenants.license_number`.
    """
    built = _columns(migrated_db, "tenants")
    for column in ("license_number", "office_address"):
        assert column in built, (
            f"tenants.{column} is declared on the Tenant model but is not "
            f"created by the migrations; the tenant resolver raises "
            f"'no such column: tenants.{column}' on every request"
        )
