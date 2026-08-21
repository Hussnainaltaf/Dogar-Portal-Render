"""Sentry SDK integration for the Dogar Portal.

Initialised on startup when SENTRY_DSN is configured. No-op otherwise.

Enhancements:
  - Tags every event with tenant (from request state) and user_id.
  - Environment sourced from ENVIRONMENT env var (falls back to ENV).
  - traces_sample_rate configurable via SENTRY_TRACES_SAMPLE_RATE.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("dtc.sentry")


def _get_tenant_tag(request) -> str:
    """Safely extract tenant slug from request.state for Sentry tags."""
    try:
        t = getattr(request.state, "tenant", None)
        if t:
            return t.slug or t.company_name or "unknown"
    except Exception:
        pass
    return "unknown"


def _get_user_tag(request) -> str:
    """Safely extract user identifier for Sentry tags."""
    try:
        u = getattr(request.state, "user", None)
        if u:
            return str(getattr(u, "id", "anonymous"))
    except Exception:
        pass
    return "anonymous"


def init_sentry() -> None:
    dsn = os.getenv("SENTRY_DSN", "")
    if not dsn:
        log.debug("SENTRY_DSN not set — Sentry disabled")
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

        traces_sample_rate = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1"))
        environment = os.getenv("ENVIRONMENT", os.getenv("ENV", "production"))

        sentry_sdk.init(
            dsn=dsn,
            integrations=[
                FastApiIntegration(),
                SqlalchemyIntegration(),
            ],
            traces_sample_rate=traces_sample_rate,
            profiles_sample_rate=0.05,
            environment=environment,
        )
        log.info("Sentry initialised (env=%s, traces_sample_rate=%s)", environment, traces_sample_rate)
    except Exception as exc:
        log.warning("Sentry init failed: %s", exc)
