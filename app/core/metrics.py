"""Prometheus metrics instrumentation for the Dogar Portal.

Registers /metrics endpoint for Prometheus scraping. The endpoint is only
exposed when the app is not in production, or when the request is from a
private IP (10.x / 172.16-31 / 192.168 / 127.0.0.1).

Custom counters:
  - dogar_ocr_validations_total
  - dogar_auth_failures_total
  - dogar_tenant_requests_total{tenant}
"""
from __future__ import annotations

import ipaddress

from fastapi import FastAPI, HTTPException, Request
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Histogram,
    generate_latest,
)
from prometheus_fastapi_instrumentator import Instrumentator

from app.core.config import settings

# Custom application counters
DOGAR_OCR_VALIDATIONS = Counter(
    "dogar_ocr_validations_total",
    "Total OCR validation attempts",
    ["status"],  # status: success | failure | rejected
)
DOGAR_AUTH_FAILURES = Counter(
    "dogar_auth_failures_total",
    "Total authentication/authorization failures",
    ["reason"],  # reason: login_failed | token_invalid | permission_denied
)
DOGAR_TENANT_REQUESTS = Counter(
    "dogar_tenant_requests_total",
    "Total requests per tenant",
    ["tenant"],
)
DOGAR_REQUEST_DURATION = Histogram(
    "dogar_request_duration_seconds",
    "Request duration histogram",
    ["method", "path"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)


class _MetricsAccess:
    """IP allowlist guard for the /metrics endpoint."""

    _allowed_networks = [
        ipaddress.ip_network("127.0.0.1/32"),
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
    ]

    def __call__(self, request: Request) -> bool:
        client = request.client.host if request.client else ""
        try:
            addr = ipaddress.ip_address(client)
            return any(addr in net for net in self._allowed_networks)
        except ValueError:
            return False


_metrics_guard = _MetricsAccess()


def _require_internal(request: Request) -> None:
    """Raise 403 if the request is not from an internal IP.
    Skip the guard when running in development (metrics are always OK).
    """
    if not settings.is_production:
        return
    if _metrics_guard(request):
        return
    raise HTTPException(status_code=403, detail="Metrics endpoint restricted to internal IPs")


def init_metrics(app: FastAPI) -> None:
    """Attach Prometheus instrumentator to the FastAPI app.
    Register a custom /metrics endpoint with IP-based access control.
    """
    instrumentator = Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
    )
    instrumentator.instrument(app)

    @app.get("/metrics", include_in_schema=False)
    async def _metrics(request: Request):
        _require_internal(request)
        from fastapi.responses import Response
        return Response(
            content=generate_latest(),
            media_type=CONTENT_TYPE_LATEST,
        )


def record_ocr_validation(status: str) -> None:
    """Increment the OCR validation counter."""
    DOGAR_OCR_VALIDATIONS.labels(status=status).inc()


def record_auth_failure(reason: str) -> None:
    """Increment the auth failure counter."""
    DOGAR_AUTH_FAILURES.labels(reason=reason).inc()


def record_tenant_request(tenant: str) -> None:
    """Increment the per-tenant request counter."""
    DOGAR_TENANT_REQUESTS.labels(tenant=tenant or "root").inc()
