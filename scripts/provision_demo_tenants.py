#!/usr/bin/env python3
"""Provision the demo tenants the browser harnesses measure against.

WHY THIS EXISTS
---------------
``scripts/verify_tenant_branding_ui.py`` proves the tenant-branding feature in
a real browser by logging into ``/t/acme`` as ``acme-admin@example.com`` with
the password ``AcmeProbe#2026``, and asserting that the office's own font and
accent are the ones the compositor actually paints. Those values were not
arbitrary -- but until now they existed ONLY as literals inside that harness,
satisfied by a tenant that happened to be sitting in this sandbox's untracked
``data/`` directory.

That is a verification that cannot be reproduced, and it fails in the most
misleading way possible. ``data/`` is deliberately excluded from the release
archive (it holds the operator's real records and every tenant's live database
-- see ``scripts/make_release_zip.sh``), so anyone who unpacks the ZIP and runs
the branding harness gets:

    FAIL: login at /t/acme returned 401

...which reads as "tenant authentication is broken" when the truth is "the
fixture this check needs was never created". A harness whose failure mode
accuses the product of a defect it does not have is worse than no harness.

So the fixture is now CODE, not local sediment: idempotent, committed, and
runnable on a clean checkout.

WHAT IT CREATES
---------------
Two demo offices, chosen to make branding divergence measurable rather than to
look plausible:

  acme -- font_key='serif',  primary='#ff0000'
      A serif face and a saturated red. Both are as far from the Dogar
      defaults (system font, #1d4ed8 blue) as the allow-list permits, which is
      the entire point: if the tenant resolver silently fell back to the root
      brand, a subtle palette would hide it and a red serif cannot.

  beta -- font_key='system', primary='#113b11'
      Deliberately shares Dogar's FONT while differing in colour, so a
      regression that keys branding off the wrong axis (applies the palette
      but not the font, or vice versa) is caught by comparing the two.

NOT SAMPLE BUSINESS DATA. These carry no candidates, demands or documents --
only the tenant row, the schema, and one admin user each. The credentials are
intentionally weak and intentionally public; they are test fixtures for a
local portal and must never be provisioned on a reachable deployment.

USAGE
    ./venv/bin/python scripts/provision_demo_tenants.py          # create
    ./venv/bin/python scripts/provision_demo_tenants.py --check   # verify only

``--check`` exits non-zero if a fixture is missing or has drifted from the
branding values the harness asserts, so the harness's assumptions are
themselves checkable without mutating anything.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.session import SessionLocal  # noqa: E402
from app.models.tenant import Tenant  # noqa: E402
from app.services.tenant_service import provision_tenant_db  # noqa: E402

# The single source of truth for what the harnesses expect. Keep in step with
# TENANT_CREDS in scripts/verify_tenant_branding_ui.py.
FIXTURES = [
    {
        "slug": "acme",
        "company_name": "Acme Overseas Employment",
        "short_name": "Acme",
        "subtitle": "Overseas Employment Promoters",
        "primary_color": "#ff0000",
        "font_key": "serif",
        "admin_email": "acme-admin@example.com",
        "admin_password": "AcmeProbe#2026",
        "admin_name": "Acme Admin",
    },
    {
        "slug": "beta",
        "company_name": "Beta Manpower Services",
        "short_name": "Beta",
        "subtitle": "Overseas Employment Promoters",
        "primary_color": "#113b11",
        "font_key": "system",
        "admin_email": "beta-admin@example.com",
        "admin_password": "BetaProbe#2026",
        "admin_name": "Beta Admin",
    },
]


def check() -> int:
    """Report whether each fixture exists with the branding the harness asserts."""
    db = SessionLocal()
    problems: list[str] = []
    try:
        for f in FIXTURES:
            t = db.query(Tenant).filter(Tenant.slug == f["slug"]).first()
            if t is None:
                problems.append(
                    f"{f['slug']}: not in the tenant registry -- run this script "
                    f"without --check to provision it")
                continue
            # Branding drift matters as much as absence: the harness asserts
            # these exact values, so a tenant that exists with the WRONG accent
            # makes the harness fail while pointing at the product.
            for field in ("primary_color", "font_key"):
                want, got = f[field], getattr(t, field, None)
                if got != want:
                    problems.append(
                        f"{f['slug']}: {field} is {got!r}, harness asserts {want!r}")
            path = t.db_path or ""
            abs_path = path if os.path.isabs(path) else os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), path)
            if not path.startswith("postgresql") and not os.path.exists(abs_path):
                problems.append(
                    f"{f['slug']}: registry points at {path!r} but that file does "
                    f"not exist -- the office would 500 on first request")
    finally:
        db.close()

    for p in problems:
        print(f"FAIL: {p}")
    if problems:
        print(f"RESULT: FAIL -- {len(problems)} problem(s)")
        return 1
    print(f"RESULT: OK -- {len(FIXTURES)} demo tenant(s) present with expected branding")
    return 0


def provision() -> int:
    db = SessionLocal()
    created, updated, skipped = [], [], []
    try:
        for f in FIXTURES:
            slug = f["slug"]
            t = db.query(Tenant).filter(Tenant.slug == slug).first()
            if t is not None:
                # Idempotent: never re-provision over a database that already
                # exists. Repairing branding drift is safe (it is registry
                # metadata); recreating the DB would destroy whatever the
                # operator has in it.
                changed = []
                for field in ("primary_color", "font_key"):
                    if getattr(t, field, None) != f[field]:
                        setattr(t, field, f[field])
                        changed.append(field)
                if changed:
                    db.commit()
                    updated.append(f"{slug} ({', '.join(changed)})")
                else:
                    skipped.append(slug)
                continue

            db_path = provision_tenant_db(
                slug=slug,
                admin_email=f["admin_email"],
                admin_password=f["admin_password"],
                admin_name=f["admin_name"],
                company_name=f["company_name"],
                short_name=f["short_name"],
            )
            t = Tenant(
                slug=slug,
                company_name=f["company_name"],
                short_name=f["short_name"],
                subtitle=f["subtitle"],
                primary_color=f["primary_color"],
                font_key=f["font_key"],
                db_path=db_path,
                features=Tenant.default_features(),
                plan="standard",
                admin_email=f["admin_email"],
                admin_password_set=True,
                status="active",
                notes="Demo/verification fixture created by "
                      "scripts/provision_demo_tenants.py. Not real business data.",
            )
            db.add(t)
            db.commit()
            created.append(slug)
    finally:
        db.close()

    for label, items in (("created", created), ("repaired", updated),
                         ("already present", skipped)):
        if items:
            print(f"{label}: {', '.join(items)}")
    # Provisioning that does not verify itself is just hope.
    return check()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="verify the fixtures without creating anything")
    args = ap.parse_args()
    raise SystemExit(check() if args.check else provision())
