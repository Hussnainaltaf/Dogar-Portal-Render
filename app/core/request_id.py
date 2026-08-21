"""Request ID middleware for distributed tracing.

Generates or propagates X-Request-ID across every request.
"""
from __future__ import annotations

import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Generate or echo X-Request-ID on every request.

    - Reads X-Request-ID from incoming headers (propagation from upstream)
    - Generates a new uuid4 if absent
    - Stores in request.state.request_id
    - Returns X-Request-ID in response headers
    - Includes in structured JSON logs via the trace_id field
    """

    async def dispatch(self, request: Request, call_next):
        req_id = request.headers.get("x-request-id", "")
        if not req_id:
            req_id = str(uuid.uuid4())
        request.state.request_id = req_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = req_id
        return response
