"""Tests for the structured form-scan schema extraction (Task 1)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.services.form_schema import all_schemas, extract_schema, list_forms


def test_all_forms_present():
    forms = list_forms()
    assert "oep_form" in forms
    assert "saudi_visa_islamabad" in forms
    assert "permission_undertaking" in forms
    assert len(forms) >= 5


def test_schema_field_shape_and_inference():
    oep = extract_schema("oep_form")
    assert oep is not None
    assert oep["field_count"] > 0
    assert oep["pages"] == [1, 2]  # OEP is a two-page form
    for f in oep["fields"]:
        # normalized coordinates in range
        for k in ("x", "y", "w", "h"):
            assert 0.0 <= f[k] <= 1.0, f"{k} out of range: {f}"
        assert f["field_type"] in {"text", "date", "char_cells", "photo", "barcode", "signature"}
        assert f["data_source"] in {"candidate", "demand", "client", "static"}
    # cnic/passport must be char_cells (boxed on the form)
    keys = {f["field_key"]: f for f in oep["fields"]}
    assert keys["cnic"]["field_type"] == "char_cells"
    assert keys["passport_no"]["field_type"] == "char_cells"
    assert keys["date_of_birth"]["field_type"] == "date"


def test_demand_source_inference():
    perm = extract_schema("permission_undertaking")
    keys = {f["field_key"]: f for f in perm["fields"]}
    assert keys["file_number"]["data_source"] == "demand"
    assert keys["sponsor_name"]["data_source"] == "demand"
    assert keys["__today__"]["data_source"] == "static"


def test_unknown_form_returns_none():
    assert extract_schema("does_not_exist") is None


def test_all_schemas_aggregate():
    a = all_schemas()
    assert a["form_count"] == len(list_forms())
    assert a["total_fields"] == sum(f["field_count"] for f in a["forms"])


# ---- endpoint auth ---------------------------------------------------------
def test_form_schemas_requires_auth(client: TestClient):
    assert client.get("/api/admin/form-schemas").status_code == 401


def test_form_schemas_admin_ok(client: TestClient, auth_headers):
    r = client.get("/api/admin/form-schemas", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["form_count"] >= 5


def test_form_schema_one_404(client: TestClient, auth_headers):
    r = client.get("/api/admin/form-schemas/nope", headers=auth_headers)
    assert r.status_code == 404
