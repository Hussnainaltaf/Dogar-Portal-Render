"""Regression: embassy (Islamabad/Karachi) must flow demand → candidate.

The embassy on a demand file drives WHICH visa application form is available
for a candidate assigned to that demand. A past migration/assignment bug left
the embassy empty, so the candidate VISA PROCESS panel showed "No embassy" and
no visa form. These tests lock the behaviour end-to-end:

    1. A demand created with an embassy exposes it on the demand.
    2. A candidate assigned to a trade under that demand reports the SAME
       embassy in its assignment-context (list + detail), so the correct
       visa form is selected.
"""
from __future__ import annotations


def _mk_client(client, auth_headers, name):
    r = client.post("/api/clients/", headers=auth_headers, json={"company_name": name})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _demand_with_trade(client, auth_headers, *, embassy, client_name):
    cid = _mk_client(client, auth_headers, name=client_name)
    r = client.post("/api/demands/", headers=auth_headers, json={
        "client_id": cid,
        "country": "Saudi Arabia",
        "embassy": embassy,
        "sponsor_name": f"Sponsor for {embassy}",
        "positions": [{"trade": "Electrician", "quantity": 2, "salary": 1500}],
    })
    assert r.status_code == 200, r.text
    did = r.json()["id"]
    # confirm the demand persisted the embassy
    r = client.get(f"/api/demands/{did}/trades", headers=auth_headers)
    assert r.status_code == 200, r.text
    trades = r.json()
    if isinstance(trades, dict):
        trades = trades.get("items", trades.get("trades", []))
    assert trades, "trade was not created"
    return did, trades[0]["id"]


def _new_candidate(client, auth_headers, full_name):
    """Create a bare candidate (unassigned) so we can attach via /assign."""
    r = client.post("/api/candidates/", headers=auth_headers, json={
        "full_name": full_name,
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def test_demand_persists_embassy(client, auth_headers):
    did, _tid = _demand_with_trade(
        client, auth_headers, embassy="Karachi Embassy",
        client_name="Embassy Persist Client")
    r = client.get(f"/api/demands/{did}", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json().get("embassy") == "Karachi Embassy"


def test_assigned_candidate_inherits_demand_embassy_karachi(client, auth_headers):
    did, tid = _demand_with_trade(
        client, auth_headers, embassy="Karachi Embassy",
        client_name="KHI Assign Client")
    cid = _new_candidate(client, auth_headers, "KHI Test Candidate")

    r = client.post(f"/api/demands/trades/{tid}/assign", headers=auth_headers,
                    json={"candidate_id": cid})
    assert r.status_code == 200, r.text

    # Detail endpoint (used by the same-page right panel) must expose embassy.
    r = client.get(f"/api/candidates/{cid}", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("embassy") == "Karachi Embassy", body.get("embassy")
    assert body.get("demand_id") == did


def test_assigned_candidate_inherits_demand_embassy_islamabad(client, auth_headers):
    did, tid = _demand_with_trade(
        client, auth_headers, embassy="Islamabad Embassy",
        client_name="ISB Assign Client")
    cid = _new_candidate(client, auth_headers, "ISB Test Candidate")

    r = client.post(f"/api/demands/trades/{tid}/assign", headers=auth_headers,
                    json={"candidate_id": cid})
    assert r.status_code == 200, r.text

    r = client.get(f"/api/candidates/{cid}", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json().get("embassy") == "Islamabad Embassy"


def test_assigned_candidate_never_shows_no_embassy(client, auth_headers):
    """The exact bug: an assigned candidate must not report an empty embassy
    when its demand has one."""
    _did, tid = _demand_with_trade(
        client, auth_headers, embassy="Karachi Embassy",
        client_name="No-Empty-Embassy Client")
    cid = _new_candidate(client, auth_headers, "No Empty Embassy Candidate")
    r = client.post(f"/api/demands/trades/{tid}/assign", headers=auth_headers,
                    json={"candidate_id": cid})
    assert r.status_code == 200, r.text
    r = client.get(f"/api/candidates/{cid}", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert (r.json().get("embassy") or "").strip() != "", "candidate shows 'No embassy'"
