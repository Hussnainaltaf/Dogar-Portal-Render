"""Tests for the app-layer realtime status endpoints (SSE + polling fallback).

There is no Supabase/Postgres realtime here; these endpoints provide an
equivalent: a version token that changes on any status edit, streamed via SSE
with a polling fallback. We test auth-gating, the health contract, and that the
version token actually moves when a tracked row changes.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.models import Client, Demand


def test_health_requires_auth(client: TestClient):
    assert client.get("/api/realtime/health").status_code == 401


def test_health_contract(client: TestClient, auth_headers):
    r = client.get("/api/realtime/health", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["transport"] == "sse"
    assert body["fallback"] == "polling"
    assert "snapshot_path" in body and "stream_path" in body


def test_snapshot_requires_auth(client: TestClient):
    assert client.get("/api/realtime/status-snapshot").status_code == 401


def test_snapshot_shape(client: TestClient, auth_headers):
    r = client.get("/api/realtime/status-snapshot", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert "version" in body and isinstance(body["version"], str)
    assert set(body["entities"]) >= {"candidates", "assignments", "demands", "documents"}


def test_version_changes_when_status_row_added(client: TestClient, auth_headers):
    """The opaque version token must move after a tracked table changes."""
    v1 = client.get("/api/realtime/status-snapshot", headers=auth_headers).json()["version"]

    db = SessionLocal()
    try:
        c = db.query(Client).first()
        if not c:
            c = Client(company_name="RT Test Co")
            db.add(c)
            db.commit()
            db.refresh(c)
        db.add(Demand(file_number="RT-1", client_id=c.id, country="UAE", status="active"))
        db.commit()
    finally:
        db.close()

    v2 = client.get("/api/realtime/status-snapshot", headers=auth_headers).json()["version"]
    assert v1 != v2, "version token should change after a demand row is inserted"
