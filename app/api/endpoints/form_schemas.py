"""Admin: structured field schemas extracted from the scanned government forms.

These two endpoints used to live inside ``app/api/endpoints/admin_migration.py``
alongside the data-migration/integrity endpoints. When the operator asked for
the "Data Migration" sidebar feature to be deleted (2026-07-30) that module went
with it -- but the form-schema endpoints are a *different* feature that nobody
asked to remove: they power the "analyse form scans -> document template"
workflow and are the machine-readable description of every government form the
portal reproduces. Deleting them as collateral damage would have silently
removed a working capability, so they were lifted into this dedicated router.

GET /api/admin/form-schemas             -- every known form + its field layout
GET /api/admin/form-schemas/{form_key}  -- one form's field layout

See section 4.11 of PROJECT_DOCUMENTATION.md.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.core.deps import require_admin
from app.models import User
from app.services.form_schema import all_schemas, extract_schema

router = APIRouter()


@router.get("/form-schemas")
def form_schemas(_: User = Depends(require_admin)):
    """Structured field schemas extracted from the scanned government forms.

    Returns every known form (OEP, Saudi visa ISB/KHI, permission undertaking,
    bank fee slip) with its normalized field layout (field_key, label,
    field_type, data_source, top-left rect fractions, page).
    """
    return all_schemas()


@router.get("/form-schemas/{form_key}")
def form_schema_one(form_key: str, _: User = Depends(require_admin)):
    """One form's normalized field layout, or 404 for an unknown key."""
    schema = extract_schema(form_key)
    if schema is None:
        raise HTTPException(status_code=404, detail=f"Unknown form '{form_key}'")
    return schema
