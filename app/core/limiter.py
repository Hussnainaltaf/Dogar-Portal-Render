"""Shared slowapi rate limiter instance.

Used across all endpoint modules to ensure consistent rate-limiting state.

AUDIT M-2 (2026-07-10): slowapi previously keyed on ``get_remote_address``
(the raw socket peer), while the login brute-force lockout keyed on the
proxy-aware ``_client_ip()`` in app/api/endpoints/auth.py. Behind a trusted
reverse proxy the two disagreed: slowapi bucketed every request under the
proxy's IP (so one abusive client could exhaust the shared limit for everyone,
or conversely evade it), while the lockout used the real client. We now expose a
single ``client_ip_key_func`` that mirrors the exact TRUSTED_PROXY_COUNT logic,
so BOTH throttles derive the client identity the same, spoof-resistant way.
"""
import os

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.core.config import settings


def client_ip_key_func(request) -> str:
    """Proxy-aware client-IP key for slowapi, consistent with auth._client_ip.

    * TRUSTED_PROXY_COUNT == 0  -> use the socket peer (X-Forwarded-For is
      attacker-controlled and must be ignored).
    * TRUSTED_PROXY_COUNT == N  -> use the Nth entry from the right of the XFF
      chain (the address our own trusted proxy actually observed).

    Falls back to slowapi's default (``get_remote_address``) if anything about
    the request object is unexpected, so rate limiting never crashes a request.
    """
    try:
        peer = request.client.host if request.client else ""
        proxies = getattr(settings, "TRUSTED_PROXY_COUNT", 0) or 0
        if proxies < 1:
            return peer or get_remote_address(request)
        fwd = request.headers.get("x-forwarded-for")
        if not fwd:
            return peer or get_remote_address(request)
        chain = [p.strip() for p in fwd.split(",") if p.strip()]
        if not chain:
            return peer or get_remote_address(request)
        idx = len(chain) - proxies
        if idx < 0:
            idx = 0
        return chain[idx] or peer or get_remote_address(request)
    except Exception:  # noqa: BLE001 -- rate-limit keying must never 500
        # Deliberate fallback. This runs on EVERY request, so it must not be
        # able to raise; slowapi's own extractor is the safe default. Not
        # logged because a malformed X-Forwarded-For is attacker-controlled and
        # would let anyone flood the log at will.
        return get_remote_address(request)


# Disable rate limiting in testing mode to avoid flaky test failures
limiter = Limiter(
    key_func=client_ip_key_func,
    enabled=os.environ.get("TESTING") != "true"
)

__all__ = ["limiter", "RateLimitExceeded", "client_ip_key_func"]
