"""Security utilities: password hashing, JWT tokens.

Uses PyJWT instead of python-jose for broader platform compatibility.
"""
import logging
from datetime import UTC, datetime, timedelta
from typing import Optional

import jwt as _jwt
from passlib.context import CryptContext

from app.core.config import settings

# Expose JWTError for any code that imports it from here
JWTError = _jwt.PyJWTError

log = logging.getLogger("dtc.security")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time-ish password check that never raises.

    Returning False (rather than propagating) on a malformed or unknown-scheme
    hash is deliberate: it keeps a corrupted row from turning into a 500 that
    would tell an attacker the account exists.
    """
    try:
        return pwd_context.verify(plain, hashed)
    except Exception as exc:  # noqa: BLE001 -- must never raise, see docstring
        # AUDIT 2026-07-31: previously a bare `return False`. If passlib were
        # ever misconfigured (missing bcrypt backend, unreadable hash scheme)
        # EVERY login in the system would fail with no diagnostic at all. The
        # exception text is logged; the password itself never is.
        log.warning("password verification could not be completed: %s", exc)
        return False


def create_access_token(subject: str, expires_minutes: Optional[int] = None,
                        tenant: Optional[str] = None) -> str:
    """Issue a signed JWT.

    ``tenant`` binds the token to the tenant slug it was issued for. This is
    a security boundary: a token minted on tenant A must NOT authenticate on
    tenant B or the root portal (where the same numeric user id belongs to a
    different person). ``None`` means the root/control portal.
    """
    expire = datetime.now(UTC) + timedelta(
        minutes=expires_minutes or settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {"sub": str(subject), "exp": expire}
    # Use empty string for the root portal so the claim is always present and
    # the equality check in deps.py is unambiguous (None vs "" are normalised).
    payload["tnt"] = tenant or ""
    return _jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> Optional[str]:
    """Return the subject (user id) or None. Kept for backward compatibility."""
    try:
        payload = _jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload.get("sub")
    except _jwt.PyJWTError:
        return None


def decode_token_full(token: str) -> Optional[dict]:
    """Return the full decoded payload (incl. ``sub`` and ``tnt``) or None."""
    try:
        return _jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except _jwt.PyJWTError:
        return None
