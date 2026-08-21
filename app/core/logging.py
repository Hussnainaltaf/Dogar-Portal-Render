"""Structured JSON logging for the Dogar Portal.

Every log line is emitted as JSON with: timestamp, level, logger, message,
per-request context (method, path, status, duration_ms, tenant, user_id),
and a trace_id (UUID per request propagated via contextvars).
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import time
import uuid
from datetime import UTC, datetime
from typing import Any, Dict

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

# Per-request trace ID stored in a contextvar so it flows through
# async handlers and log formatters without manual threading.
_trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("dtc_trace_id", default="")


class JSONFormatter(logging.Formatter):
    """Emit logs as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj: Dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": getattr(record, "trace_id", "") or _trace_id_var.get(""),
            "source": {
                "file": record.pathname,
                "line": record.lineno,
            },
        }
        if hasattr(record, "extra"):
            log_obj.update(record.extra)
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj, default=str)


def configure_logging() -> None:
    """Set up the root logger with JSON formatting for all handlers."""
    root = logging.getLogger()
    # Remove any pre-existing default handler so we don't get duplicate lines
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    root.addHandler(handler)

    # LOG_LEVEL env var overrides default INFO
    level_str = os.getenv("LOG_LEVEL", "INFO").upper()
    root.setLevel(getattr(logging, level_str, logging.INFO))

    # Keep noisy third-party libs quiet
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("alembic").setLevel(logging.INFO)


def get_trace_id() -> str:
    """Return the current request's trace_id or a new one."""
    return _trace_id_var.get(None) or str(uuid.uuid4())


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every request with method, path, status, duration_ms, tenant, user_id, trace_id."""

    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        # Prefer the request_id from RequestIdMiddleware if it exists
        trace_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        _trace_id_var.set(trace_id)

        tenant = ""
        user_id = None
        try:
            t = getattr(request.state, "tenant", None)
            if t:
                tenant = t.slug or t.company_name or ""
        except Exception:
            pass
        try:
            u = getattr(request.state, "user", None)
            if u:
                user_id = getattr(u, "id", None)
        except Exception:
            pass

        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)

        log = logging.getLogger("dtc.request")
        log.info(
            "%(method)s %(path)s → %(status)s %(duration_ms)sms",
            {
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
                "tenant": tenant,
                "user_id": user_id,
                "trace_id": trace_id,
            },
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
                "tenant": tenant,
                "user_id": user_id,
                "trace_id": trace_id,
            },
        )
        return response
