"""Create-Demand wizard: auto file number + one-call visa positions."""
from __future__ import annotations


def _mk_client(client, auth_headers, name="Wizard Test Client"):
    r = client.post("/api/clients/", headers=auth_headers, json={"company_name": name})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_next_file_number_endpoint(client, auth_headers):
    r = client.get("/api/demands/config/next-file-number", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["display"], "display file number must not be empty"
    assert body["next_seq"], "next_seq must not be empty"


def test_create_demand_with_positions_creates_trades(client, auth_headers):
    cid = _mk_client(client, auth_headers)
    r = client.post("/api/demands/", headers=auth_headers, json={
        "client_id": cid,
        "receiving_date": "2026-07-02",
        "visa_number": "1300000123",
        "bataka_number": "998877",
        "visa_issue_date": "2026-06-15",
        "country": "Saudi Arabia",
        "embassy": "Karachi Embassy",
        "sponsor_name": "Test Sponsor LLC",
        "positions": [
            {"trade": "Electrician", "quantity": 3, "salary": 1500},
            {"trade": "Plumber", "quantity": 2},
            {"trade": "", "quantity": 5},          # incomplete → skipped
            {"trade": "Driver", "quantity": "bad"}, # bad qty → defaults to 1
        ],
    })
    assert r.status_code == 200, r.text
    did = r.json()["id"]

    r = client.get(f"/api/demands/{did}/trades", headers=auth_headers)
    assert r.status_code == 200, r.text
    trades = r.json()
    if isinstance(trades, dict):
        trades = trades.get("items", trades.get("trades", []))
    by_name = {t["trade"]: t for t in trades}
    assert set(by_name) == {"Electrician", "Plumber", "Driver"}, by_name
    assert by_name["Electrician"]["quantity"] == 3
    assert float(by_name["Electrician"]["salary"]) == 1500.0
    assert by_name["Plumber"]["quantity"] == 2
    assert by_name["Driver"]["quantity"] == 1  # bad value defaulted


def test_create_demand_without_positions_still_works(client, auth_headers):
    """Regression guard: old payloads (no positions key) behave exactly as before."""
    cid = _mk_client(client, auth_headers, name="No Positions Client")
    r = client.post("/api/demands/", headers=auth_headers, json={
        "client_id": cid, "sponsor_name": "Plain Sponsor",
    })
    assert r.status_code == 200, r.text
    did = r.json()["id"]
    r = client.get(f"/api/demands/{did}/trades", headers=auth_headers)
    trades = r.json()
    if isinstance(trades, dict):
        trades = trades.get("items", trades.get("trades", []))
    assert trades == [] or len(trades) == 0


def test_auto_file_number_increments(client, auth_headers):
    """Two consecutive demands get strictly increasing file numbers."""
    cid = _mk_client(client, auth_headers, name="Seq Client")
    r1 = client.post("/api/demands/", headers=auth_headers, json={"client_id": cid})
    r2 = client.post("/api/demands/", headers=auth_headers, json={"client_id": cid})
    assert r1.status_code == 200 and r2.status_code == 200
    n1 = int(str(r1.json()["file_number"]).lstrip("0") or 0)
    n2 = int(str(r2.json()["file_number"]).lstrip("0") or 0)
    assert n2 == n1 + 1, f"file numbers must auto-increment: {n1} → {n2}"


def test_wizard_validation_returns_field_issue_paths(client, auth_headers):
    """The SPA's strict mode returns FastAPI-compatible field issues."""
    r = client.post(
        "/api/demands/",
        headers=auth_headers,
        json={"validation_mode": "wizard", "client_id": "", "positions": []},
    )
    assert r.status_code == 422, r.text
    issues = r.json()["detail"]
    by_path = {".".join(str(part) for part in issue["loc"][1:]): issue["msg"] for issue in issues}
    assert by_path["client_id"] == "Client is required"
    assert by_path["receiving_date"] == "Receiving date is required"
    assert by_path["positions.0.trade"] == "Add at least one visa position"
