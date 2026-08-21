"""
Messaging API — real WhatsApp / SMS delivery via Twilio (Task 9).

Replaces the old client-side `wa.me` quick-links with a genuine server-side
provider integration. All endpoints require authentication; Twilio credentials
live only in server settings and are never sent to the browser.

Routes (mounted at /api/messaging):
    GET  /status                 → provider status (no secrets)
    POST /send                   → send an SMS or WhatsApp message
    GET  /logs                   → recent outbound message history
    GET  /candidate/{id}/logs    → message history for one candidate
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.core.tenancy import get_tenant_db
from app.models import User
from app.models.lookups import MessageLog
from app.services.messaging_service import provider_status, send_message

router = APIRouter()


class SendMessageRequest(BaseModel):
    to: str = Field(..., description="Recipient phone number (any format; normalised to E.164)")
    body: str = Field(..., min_length=1, max_length=1600)
    channel: str = Field("sms", pattern="^(sms|whatsapp)$")
    candidate_id: Optional[int] = None


@router.get("/status")
def messaging_status(user: User = Depends(get_current_user)):
    """Non-secret provider status for the Settings / messaging panel."""
    return provider_status()


@router.post("/send")
def send(
    payload: SendMessageRequest,
    db: Session = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    """Send a real SMS / WhatsApp message through Twilio and log the attempt.

    Returns the delivery result. Even failures are logged (status=failed) so the
    audit trail is complete. When Twilio is not configured the service runs in
    dry-run mode (status=dry_run) and no real message is sent.
    """
    result = send_message(payload.to, payload.body, channel=payload.channel)

    # Persist every attempt for the audit trail.
    log = MessageLog(
        channel=result.channel,
        to_number=result.to,
        body=payload.body,
        status=result.status,
        provider_sid=result.provider_sid or "",
        error=result.error or "",
        candidate_id=payload.candidate_id,
        sent_by=getattr(user, "id", None),
    )
    db.add(log)
    db.commit()
    db.refresh(log)

    if not result.ok:
        # 502 for provider/network failures, 400 for bad input.
        code = 400 if result.status == "invalid" else 502
        raise HTTPException(code, detail=result.error or "Message could not be sent")

    return {
        "id": log.id,
        **result.to_dict(),
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }


def _serialize_log(m: MessageLog) -> dict:
    return {
        "id": m.id,
        "channel": m.channel,
        "to": m.to_number,
        "body": m.body,
        "status": m.status,
        "provider_sid": m.provider_sid or None,
        "error": m.error or None,
        "candidate_id": m.candidate_id,
        "sent_by": m.sent_by,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


@router.get("/logs")
def list_logs(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    rows = (
        db.query(MessageLog)
        .order_by(MessageLog.id.desc())
        .limit(limit)
        .all()
    )
    return [_serialize_log(m) for m in rows]


@router.get("/candidate/{candidate_id}/logs")
def candidate_logs(
    candidate_id: int,
    db: Session = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    rows = (
        db.query(MessageLog)
        .filter(MessageLog.candidate_id == candidate_id)
        .order_by(MessageLog.id.desc())
        .all()
    )
    return [_serialize_log(m) for m in rows]
