"""Task 9 — real WhatsApp / SMS (Twilio) messaging integration tests.

Covers:
  * phone normalisation to E.164
  * dry-run behaviour when Twilio is not configured
  * live send path with Twilio mocked (no real network / cost)
  * API auth requirement + message-log persistence
"""
from __future__ import annotations

from unittest import mock

import pytest

from app.services import messaging_service as ms


# ── normalize_phone ────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("+923001234567", "+923001234567"),
    ("0300 1234567", "+923001234567"),
    ("0300-123-4567", "+923001234567"),
    ("00923001234567", "+923001234567"),
    ("923001234567", "+923001234567"),
    ("(0300) 1234567", "+923001234567"),
])
def test_normalize_phone(raw, expected):
    assert ms.normalize_phone(raw) == expected


def test_normalize_phone_invalid():
    assert ms.normalize_phone("") is None
    assert ms.normalize_phone("abc") is None


# ── dry-run when unconfigured ──────────────────────────────────────────────
def test_send_dry_run_when_unconfigured(monkeypatch):
    monkeypatch.setattr(ms.settings, "TWILIO_ACCOUNT_SID", "", raising=False)
    monkeypatch.setattr(ms.settings, "TWILIO_AUTH_TOKEN", "", raising=False)
    res = ms.send_message("0300 1234567", "Hello", channel="sms")
    assert res.ok is True
    assert res.status == "dry_run"
    assert res.to == "+923001234567"
    assert res.provider_sid is None


def test_send_rejects_bad_channel():
    res = ms.send_message("+923001234567", "hi", channel="pigeon")
    assert res.ok is False
    assert res.status == "invalid"


def test_send_rejects_empty_body(monkeypatch):
    monkeypatch.setattr(ms.settings, "TWILIO_ACCOUNT_SID", "", raising=False)
    res = ms.send_message("+923001234567", "   ", channel="sms")
    assert res.ok is False
    assert res.status == "invalid"


# ── live send path with Twilio mocked ──────────────────────────────────────
def test_send_live_sms_success(monkeypatch):
    monkeypatch.setattr(ms.settings, "TWILIO_ACCOUNT_SID", "ACxxxx", raising=False)
    monkeypatch.setattr(ms.settings, "TWILIO_AUTH_TOKEN", "tok", raising=False)
    monkeypatch.setattr(ms.settings, "TWILIO_SMS_FROM", "+12025550123", raising=False)
    monkeypatch.setattr(ms.settings, "TWILIO_MESSAGING_SERVICE_SID", "", raising=False)

    fake_resp = mock.Mock()
    fake_resp.status_code = 201
    fake_resp.json.return_value = {"sid": "SM123", "status": "queued", "num_segments": "1"}

    with mock.patch.object(ms.httpx, "post", return_value=fake_resp) as m:
        res = ms.send_message("0300 1234567", "Your visa is ready", channel="sms")

    assert res.ok is True
    assert res.status == "queued"
    assert res.provider_sid == "SM123"
    # Correct Twilio endpoint + payload
    args, kwargs = m.call_args
    assert "Messages.json" in args[0]
    assert kwargs["data"]["To"] == "+923001234567"
    assert kwargs["data"]["From"] == "+12025550123"


def test_send_live_whatsapp_prefixes_addresses(monkeypatch):
    monkeypatch.setattr(ms.settings, "TWILIO_ACCOUNT_SID", "ACxxxx", raising=False)
    monkeypatch.setattr(ms.settings, "TWILIO_AUTH_TOKEN", "tok", raising=False)
    monkeypatch.setattr(ms.settings, "TWILIO_WHATSAPP_FROM", "+14155238886", raising=False)

    fake_resp = mock.Mock()
    fake_resp.status_code = 201
    fake_resp.json.return_value = {"sid": "WA1", "status": "sent"}

    with mock.patch.object(ms.httpx, "post", return_value=fake_resp) as m:
        res = ms.send_message("0300 1234567", "hi", channel="whatsapp")

    assert res.ok is True
    kwargs = m.call_args.kwargs
    assert kwargs["data"]["From"] == "whatsapp:+14155238886"
    assert kwargs["data"]["To"] == "whatsapp:+923001234567"


def test_send_live_failure_surfaces_error(monkeypatch):
    monkeypatch.setattr(ms.settings, "TWILIO_ACCOUNT_SID", "ACxxxx", raising=False)
    monkeypatch.setattr(ms.settings, "TWILIO_AUTH_TOKEN", "tok", raising=False)
    monkeypatch.setattr(ms.settings, "TWILIO_SMS_FROM", "+12025550123", raising=False)

    fake_resp = mock.Mock()
    fake_resp.status_code = 400
    fake_resp.json.return_value = {"code": 21211, "message": "Invalid 'To' phone number"}

    with mock.patch.object(ms.httpx, "post", return_value=fake_resp):
        res = ms.send_message("0300 1234567", "hi", channel="sms")

    assert res.ok is False
    assert res.status == "failed"
    assert "Invalid" in (res.error or "")


# ── provider_status leaks no secrets ───────────────────────────────────────
def test_provider_status_hides_credentials(monkeypatch):
    monkeypatch.setattr(ms.settings, "TWILIO_ACCOUNT_SID", "ACsecret", raising=False)
    monkeypatch.setattr(ms.settings, "TWILIO_AUTH_TOKEN", "topsecret", raising=False)
    monkeypatch.setattr(ms.settings, "TWILIO_SMS_FROM", "+12025550123", raising=False)
    st = ms.provider_status()
    assert st["configured"] is True
    assert st["mode"] == "live"
    # Never expose the SID or token
    assert "ACsecret" not in str(st)
    assert "topsecret" not in str(st)


# ── API layer ──────────────────────────────────────────────────────────────
def test_messaging_status_requires_auth(client):
    r = client.get("/api/messaging/status")
    assert r.status_code in (401, 403)


def test_messaging_send_requires_auth(client):
    r = client.post("/api/messaging/send", json={"to": "+923001234567", "body": "hi", "channel": "sms"})
    assert r.status_code in (401, 403)


def test_messaging_send_dry_run_and_logs(client, auth_headers, monkeypatch):
    # Force unconfigured → dry-run so no real message is sent.
    monkeypatch.setattr(ms.settings, "TWILIO_ACCOUNT_SID", "", raising=False)
    monkeypatch.setattr(ms.settings, "TWILIO_AUTH_TOKEN", "", raising=False)

    r = client.post(
        "/api/messaging/send",
        headers=auth_headers,
        json={"to": "0300 1234567", "body": "Interview on Monday", "channel": "whatsapp", "candidate_id": 1},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "dry_run"
    assert body["to"] == "+923001234567"
    log_id = body["id"]
    assert log_id

    # It should appear in the candidate's message log.
    r2 = client.get("/api/messaging/candidate/1/logs", headers=auth_headers)
    assert r2.status_code == 200
    logs = r2.json()
    assert any(m["id"] == log_id for m in logs)


def test_messaging_send_invalid_number_returns_400(client, auth_headers, monkeypatch):
    monkeypatch.setattr(ms.settings, "TWILIO_ACCOUNT_SID", "", raising=False)
    r = client.post(
        "/api/messaging/send",
        headers=auth_headers,
        json={"to": "notaphone!!!", "body": "hi", "channel": "sms"},
    )
    assert r.status_code == 400
