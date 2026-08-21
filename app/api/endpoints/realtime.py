"""Realtime status endpoints (app-layer, no Supabase).

Keeps candidate / demand-file / document statuses fresh in the UI with graceful
degradation:

GET /api/realtime/health           — is the stream transport available? poll cfg
GET /api/realtime/status-snapshot  — one-shot version token + per-entity counts
                                     (the POLLING FALLBACK source)
GET /api/realtime/status-stream    — SSE: emits the version token ~1x/sec; the
                                     client refetches whenever the token changes.
                                     If this connection drops, the frontend hook
                                     automatically switches to polling the
                                     snapshot endpoint so statuses stay live.

All require an authenticated user (get_current_user). They are read-only.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.core.tenancy import get_tenant_db
from app.models import User
from app.services.status_stream import health, status_snapshot, status_version

router = APIRouter()


@router.get("/health")
def realtime_health(_: User = Depends(get_current_user)):
    return health()


@router.get("/status-snapshot")
def realtime_snapshot(
    db: Session = Depends(get_tenant_db),
    _: User = Depends(get_current_user),
):
    """Polling fallback: current status version + per-entity breakdown."""
    return status_snapshot(db)


@router.get("/status-stream")
async def realtime_stream(
    db: Session = Depends(get_tenant_db),
    _: User = Depends(get_current_user),
):
    """SSE stream of the status version token.

    Emits an event only when the token actually changes (plus a periodic
    heartbeat comment so proxies keep the connection open). The client compares
    tokens and refetches on change. On disconnect the client falls back to
    polling /status-snapshot.
    """
    async def gen():
        # Tell EventSource to retry after 3s if the connection drops.
        yield "retry: 3000\n\n"
        last = None
        heartbeat = 0
        while True:
            try:
                current = status_version(db)
            except Exception:
                # DB hiccup — emit a heartbeat and let the client keep polling.
                yield ": db-unavailable\n\n"
                await asyncio.sleep(2.0)
                continue
            if current != last:
                last = current
                payload = json.dumps({"version": current})
                yield f"event: status\ndata: {payload}\n\n"
                heartbeat = 0
            else:
                heartbeat += 1
                # Comment heartbeat every ~15s keeps intermediaries from closing
                # the idle connection.
                if heartbeat >= 15:
                    yield ": heartbeat\n\n"
                    heartbeat = 0
            await asyncio.sleep(1.0)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
