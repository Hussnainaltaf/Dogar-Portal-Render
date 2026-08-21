"""Generic CRUD endpoints for sidebar lookup tables (Visa Categories, Embassies,
Cities, Medical Centers, Contacts, Depositors, Service Charges).
Each endpoint follows the same simple pattern: list / create / update / delete.

AUTHORIZATION (audit 2026-07-31, High -- privilege escalation)
--------------------------------------------------------------
Every endpoint in this module used to be guarded by nothing but
``get_current_user``, i.e. "any logged-in user". That silently granted *write*
access to 7 lookup tables x 4 verbs plus the 4 depositor endpoints to every
Staff account, even though PERMISSION_CATALOG declares ``settings:lookups`` and
``depositors:view|edit`` and the Staff preset contains none of them. It was
reproduced against the running server: a Staff token with an empty
``settings:*``/``depositors:*`` permission set successfully created a City and a
Depositor. Staff could therefore delete the trades, cities, embassies and
medical centres that candidate and demand records depend on.

The split now applied:

* **Reads of the generic lookups stay authenticated-only.** Candidate and demand
  forms need trades/cities/embassies to render, Staff legitimately holds
  ``candidates:create``, and the catalogue has no ``lookups:view`` key to gate
  them with. Gating reads would break the core Staff workflow to fix a problem
  that only exists on writes.
* **Writes require ``settings:lookups``** -- in the Manager preset, absent from
  Staff, which is exactly the intent the catalogue already declared.
* **Depositors require ``depositors:view`` / ``depositors:edit``**, including
  reads: depositors are consumed only by the dedicated Depositors screen, which
  is already nav-gated on ``depositors:view``, so nothing else regresses.

``require_permission`` admits admin and super-admin through the ``"*"``
wildcard, so Admin behaviour is unchanged. This IS a deliberate behaviour change
for Staff: it closes the escalation. See section 4.12 of PROJECT_DOCUMENTATION.md.
"""
from typing import Optional, Type

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.core.permissions import require_permission
from app.core.tenancy import get_tenant_db
from app.models.lookups import (
    City,
    Contact,
    Depositor,
    Embassy,
    MedicalCenter,
    ServiceCharge,
    VisaCategory,
)

router = APIRouter()


# ----- helpers -----
def _to_dict(row):
    return {
        c.name: (getattr(row, c.name).isoformat() if hasattr(getattr(row, c.name), "isoformat") else getattr(row, c.name))
        for c in row.__table__.columns
    }


def _crud(model_class: Type, allowed_fields: list, search_field: str = "name"):
    """Build 4 CRUD endpoint functions for a model. Returns dict for inclusion in router."""

    def list_items(
        db: Session = Depends(get_tenant_db),
        user=Depends(get_current_user),
        q: Optional[str] = Query(None, description="Search term"),
        skip: int = 0,
        limit: int = 200,
    ):
        query = db.query(model_class)
        if q and hasattr(model_class, search_field):
            query = query.filter(getattr(model_class, search_field).ilike(f"%{q}%"))
        total = query.count()
        rows = query.order_by(model_class.id.desc()).offset(skip).limit(limit).all()
        return {"total": total, "items": [_to_dict(r) for r in rows]}

    def create_item(payload: dict, db: Session = Depends(get_tenant_db),
                    user=Depends(require_permission("settings:lookups"))):
        data = {k: v for k, v in payload.items() if k in allowed_fields}
        if not data.get(search_field):
            raise HTTPException(400, f"{search_field} is required")
        row = model_class(**data)
        db.add(row); db.commit(); db.refresh(row)
        return _to_dict(row)

    def update_item(item_id: int, payload: dict, db: Session = Depends(get_tenant_db),
                    user=Depends(require_permission("settings:lookups"))):
        row = db.query(model_class).filter(model_class.id == item_id).first()
        if not row:
            raise HTTPException(404, "Not found")
        for k, v in payload.items():
            if k in allowed_fields:
                setattr(row, k, v)
        db.commit(); db.refresh(row)
        return _to_dict(row)

    def delete_item(item_id: int, db: Session = Depends(get_tenant_db),
                    user=Depends(require_permission("settings:lookups"))):
        row = db.query(model_class).filter(model_class.id == item_id).first()
        if not row:
            raise HTTPException(404, "Not found")
        db.delete(row); db.commit()
        return {"ok": True}

    return list_items, create_item, update_item, delete_item


# ===== Build endpoints for each lookup =====
_LOOKUPS = [
    ("visa-categories", VisaCategory, ["name", "name_arabic", "code", "description", "is_active"]),
    ("trades",          VisaCategory, ["name", "name_arabic", "code", "description", "is_active"]),
    ("embassies",       Embassy,      ["name", "country", "city", "address", "phone"]),
    ("cities",          City,         ["name", "province", "country"]),
    ("medical-centers", MedicalCenter,["name", "city", "address", "phone"]),
    ("contacts",        Contact,      ["name", "company", "email", "phone", "note"]),
    ("service-charges", ServiceCharge,["name", "amount", "description", "is_active"]),
]

for slug, model_class, fields in _LOOKUPS:
    l, c, u, d = _crud(model_class, fields)
    router.add_api_route(f"/{slug}",            l, methods=["GET"],    name=f"list_{slug}")
    router.add_api_route(f"/{slug}",            c, methods=["POST"],   name=f"create_{slug}")
    router.add_api_route(f"/{slug}/{{item_id}}",u, methods=["PUT"],    name=f"update_{slug}")
    router.add_api_route(f"/{slug}/{{item_id}}",d, methods=["DELETE"], name=f"delete_{slug}")


# ===== Depositors (special: first_name is the required search field) =====
def _list_depositors(
    db: Session = Depends(get_tenant_db),
    user=Depends(require_permission("depositors:view")),
    q: Optional[str] = None, skip: int = 0, limit: int = 200,
):
    query = db.query(Depositor)
    if q:
        query = query.filter(
            (Depositor.first_name.ilike(f"%{q}%")) |
            (Depositor.last_name.ilike(f"%{q}%")) |
            (Depositor.cnic.ilike(f"%{q}%"))
        )
    total = query.count()
    rows = query.order_by(Depositor.id.desc()).offset(skip).limit(limit).all()
    return {"total": total, "items": [_to_dict(r) for r in rows]}


def _create_depositor(payload: dict, db: Session = Depends(get_tenant_db),
                      user=Depends(require_permission("depositors:edit"))):
    if not payload.get("first_name"):
        raise HTTPException(400, "first_name is required")
    allowed = ["first_name", "last_name", "full_name", "cnic", "mobile", "address"]
    data = {k: v for k, v in payload.items() if k in allowed}
    if not data.get("full_name"):
        data["full_name"] = f"{data.get('first_name','')} {data.get('last_name','')}".strip()
    row = Depositor(**data); db.add(row); db.commit(); db.refresh(row)
    return _to_dict(row)


def _update_depositor(item_id: int, payload: dict, db: Session = Depends(get_tenant_db),
                      user=Depends(require_permission("depositors:edit"))):
    row = db.query(Depositor).filter(Depositor.id == item_id).first()
    if not row: raise HTTPException(404, "Not found")
    allowed = ["first_name", "last_name", "full_name", "cnic", "mobile", "address"]
    for k, v in payload.items():
        if k in allowed: setattr(row, k, v)
    db.commit(); db.refresh(row); return _to_dict(row)


def _delete_depositor(item_id: int, db: Session = Depends(get_tenant_db),
                      user=Depends(require_permission("depositors:edit"))):
    row = db.query(Depositor).filter(Depositor.id == item_id).first()
    if not row: raise HTTPException(404, "Not found")
    db.delete(row); db.commit(); return {"ok": True}


router.add_api_route("/depositors",            _list_depositors,   methods=["GET"])
router.add_api_route("/depositors",            _create_depositor,  methods=["POST"])
router.add_api_route("/depositors/{item_id}",  _update_depositor,  methods=["PUT"])
router.add_api_route("/depositors/{item_id}",  _delete_depositor,  methods=["DELETE"])
