"""Candidate resumable-draft wizard: create → save 7 steps → finalize.

AUDIT FIX (2026-07-07, FIX 2 / Option A): the frontend AssignWizard now drives
the /candidates/wizard/* draft API and covers all 7 backend WIZARD_STEPS
(including the previously UI-less Medical & Protector and Visa & Travel steps).
These tests lock in the contract the frontend depends on:

  * step count / labels match TOTAL_WIZARD_STEPS
  * a draft can be opened on a trade, each step saved, then finalised
  * finalize creates the candidate, persists the medical/visa columns, and
    auto-assigns the candidate to the trade
  * the finalize auto-assign honours the trade's slot capacity (parity with the
    standalone /demands/trades/{id}/assign endpoint)
"""
from __future__ import annotations


def _mk_client(client, auth_headers, name="Wizard Cand Client"):
    r = client.post("/api/clients/", headers=auth_headers, json={"company_name": name})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _mk_demand_with_trade(client, auth_headers, *, trade="Electrician", quantity=1):
    """Create a demand file with a single trade and return (demand_id, trade_id)."""
    cid = _mk_client(client, auth_headers)
    r = client.post("/api/demands/", headers=auth_headers, json={
        "client_id": cid,
        "sponsor_name": "Wizard Sponsor",
        "positions": [{"trade": trade, "quantity": quantity, "salary": 1500}],
    })
    assert r.status_code == 200, r.text
    did = r.json()["id"]
    r = client.get(f"/api/demands/{did}/trades", headers=auth_headers)
    assert r.status_code == 200, r.text
    trades = r.json()
    if isinstance(trades, dict):
        trades = trades.get("items", trades.get("trades", []))
    assert trades, "expected at least one trade"
    return did, trades[0]["id"]


def test_wizard_step_metadata_has_seven_steps(client, auth_headers):
    """The /candidates/stages endpoint advertises exactly 7 wizard steps —
    the number the frontend AssignWizard renders."""
    r = client.get("/api/candidates/stages", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_wizard_steps"] == 7, body
    labels = [s["label"] for s in body["steps"]]
    assert labels == [
        "Personal Info", "Identification", "Employment", "Next of Kin",
        "Medical & Protector", "Visa & Travel", "Charge Summary",
    ], labels


def test_wizard_full_flow_creates_and_assigns_candidate(client, auth_headers):
    _did, trade_id = _mk_demand_with_trade(client, auth_headers)

    # 1) Open a draft pre-linked to the trade.
    r = client.post("/api/candidates/wizard", headers=auth_headers,
                    json={"job_category_id": trade_id})
    assert r.status_code == 200, r.text
    w = r.json()
    wid = w["id"]
    assert w["current_step"] == 1
    assert w["total_steps"] == 7

    # 2) Save all 7 steps, including the two previously-UI-less domains.
    step_data = {
        1: {"full_name": "Ali Raza", "father_name": "Karam Din", "gender": "Male",
            "date_of_birth": "1990-05-01", "nationality": "PAKISTANI"},
        2: {"passport_no": "AB1234567", "cnic": "34601-1234567-1"},
        3: {"permission_no": "DTC/LHR/9001/12", "qualification": "Matric",
            "profession": "Electrician", "age_employee": "34"},
        4: {"next_of_kin_name": "Bilal Raza", "next_of_kin_relation": "Brother"},
        5: {"protector_no": "PRT-777", "medical_center": "GAMCA Lahore",
            "gamca_number": "GC-555", "medical_courier_name": "TCS"},
        6: {"e_number": "E-99887", "flight_no": "SV-728", "destination": "Riyadh",
            "ticket_no": "TK-12345"},
        7: {"salary": "1500", "price": "250", "ticket_included": "Yes"},
    }
    for step, data in step_data.items():
        advance = step < 7
        r = client.patch(f"/api/candidates/wizard/{wid}/step/{step}",
                         headers=auth_headers, json={"data": data, "advance": advance})
        assert r.status_code == 200, f"step {step}: {r.text}"

    # 3) Finalize → creates candidate + auto-assigns to the trade.
    r = client.post(f"/api/candidates/wizard/{wid}/finalize", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    cand = body["candidate"]
    assert body["wizard"]["is_finalized"] is True
    assert cand["full_name"] == "Ali Raza"
    # Medical & Protector (step 5) persisted.
    assert cand["protector_no"] == "PRT-777"
    assert cand["medical_center"] == "GAMCA Lahore"
    assert cand["gamca_number"] == "GC-555"
    # Visa & Travel (step 6) persisted.
    assert cand["e_number"] == "E-99887"
    assert cand["destination"] == "Riyadh"
    assert cand["ticket_no"] == "TK-12345"

    # 4) The candidate must now appear assigned to the trade.
    _did2, _tid = _did, trade_id
    r = client.get(f"/api/demands/{_did2}/trades", headers=auth_headers)
    trades = r.json()
    if isinstance(trades, dict):
        trades = trades.get("items", trades.get("trades", []))
    tr = next(t for t in trades if t["id"] == trade_id)
    assigned_ids = [c["id"] for c in (tr.get("candidates") or [])]
    assert cand["id"] in assigned_ids, tr


def test_wizard_finalize_requires_full_name(client, auth_headers):
    _did, trade_id = _mk_demand_with_trade(client, auth_headers)
    r = client.post("/api/candidates/wizard", headers=auth_headers,
                    json={"job_category_id": trade_id})
    wid = r.json()["id"]
    # Save only a father name — full_name missing → finalize must 400.
    client.patch(f"/api/candidates/wizard/{wid}/step/1", headers=auth_headers,
                 json={"data": {"father_name": "X"}, "advance": False})
    r = client.post(f"/api/candidates/wizard/{wid}/finalize", headers=auth_headers)
    assert r.status_code == 400, r.text
    assert "full name" in r.text.lower()


def test_wizard_finalize_respects_slot_capacity(client, auth_headers):
    """A trade with quantity=1: the second finalize must be refused (slot full),
    matching the standalone /assign endpoint's guard."""
    _did, trade_id = _mk_demand_with_trade(client, auth_headers, quantity=1)

    def _run_wizard(name):
        r = client.post("/api/candidates/wizard", headers=auth_headers,
                        json={"job_category_id": trade_id})
        wid = r.json()["id"]
        client.patch(f"/api/candidates/wizard/{wid}/step/1", headers=auth_headers,
                     json={"data": {"full_name": name, "father_name": "Dad"}, "advance": False})
        return client.post(f"/api/candidates/wizard/{wid}/finalize", headers=auth_headers)

    r1 = _run_wizard("First Cand")
    assert r1.status_code == 200, r1.text

    r2 = _run_wizard("Second Cand")
    assert r2.status_code == 400, r2.text
    assert "no slots" in r2.text.lower() or "fully assigned" in r2.text.lower()


def test_candidate_multi_filters_and_assignment_pagination(client, auth_headers):
    """Multi-select filters combine correctly and assignments never shorten pages."""
    client_one = _mk_client(client, auth_headers, "Filter Client One")
    client_two = _mk_client(client, auth_headers, "Filter Client Two")

    def make_demand(client_id, sponsor, trade):
        response = client.post(
            "/api/demands/",
            headers=auth_headers,
            json={
                "client_id": client_id,
                "sponsor_name": sponsor,
                "positions": [{"trade": trade, "quantity": 3, "salary": 1400}],
            },
        )
        assert response.status_code == 200, response.text
        demand_id = response.json()["id"]
        trades = client.get(
            f"/api/demands/{demand_id}/trades",
            headers=auth_headers,
        ).json()
        return demand_id, trades[0]["id"]

    demand_one, electrician = make_demand(
        client_one, "Filter Sponsor One", "Filter Electrician"
    )
    demand_two, plumber = make_demand(
        client_two, "Filter Sponsor Two", "Filter Plumber"
    )

    candidate_ids = []
    for suffix in ("Alpha", "Beta"):
        response = client.post(
            "/api/candidates/",
            headers=auth_headers,
            json={"full_name": f"Multi Filter Probe {suffix}", "status": "new"},
        )
        assert response.status_code == 200, response.text
        candidate_ids.append(response.json()["id"])

    for trade_id, candidate_id in (
        (electrician, candidate_ids[0]),
        (plumber, candidate_ids[0]),
        (plumber, candidate_ids[1]),
    ):
        response = client.post(
            f"/api/demands/trades/{trade_id}/assign",
            headers=auth_headers,
            json={"candidate_id": candidate_id},
        )
        assert response.status_code == 200, response.text

    common = {"q": "Multi Filter Probe", "statuses": "docs_pending"}

    by_client = client.get(
        "/api/candidates/",
        headers=auth_headers,
        params={**common, "clients": str(client_one)},
    )
    assert by_client.status_code == 200, by_client.text
    assert [item["id"] for item in by_client.json()["items"]] == [candidate_ids[0]]

    by_demand_and_category = client.get(
        "/api/candidates/",
        headers=auth_headers,
        params={
            **common,
            "demands": str(demand_two),
            "categories": "Filter Plumber",
        },
    )
    assert by_demand_and_category.status_code == 200, by_demand_and_category.text
    assert {item["id"] for item in by_demand_and_category.json()["items"]} == set(candidate_ids)

    pages = []
    for skip in (0, 1):
        response = client.get(
            "/api/candidates/",
            headers=auth_headers,
            params={**common, "limit": 1, "skip": skip},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 2
        assert len(body["items"]) == 1
        pages.append(body["items"][0]["id"])

    assert set(pages) == set(candidate_ids)
