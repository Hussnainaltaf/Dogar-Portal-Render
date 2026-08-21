"""Request dependencies -- get current user from JWT cookie or header."""
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.security import decode_token_full
from app.core.tenancy import get_tenant_db
from app.models import User


def get_token_from_request(request: Request) -> Optional[str]:
    # Try Authorization header first (API clients)
    auth = request.headers.get("Authorization") or request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    # Try HttpOnly cookie (browser sessions) -- preferred for web clients.
    # NOTE: query-param token acceptance was intentionally removed (H-05).
    # Tokens in URLs are logged by Nginx/uvicorn, stored in browser history,
    # and leaked via the Referer header to third-party origins. Use cookies
    # or Authorization headers exclusively.
    return request.cookies.get("access_token")


def get_current_user_optional(
    request: Request,
    db: Session = Depends(get_tenant_db),
) -> Optional[User]:
    # NOTE: users live in the PER-TENANT database (each tenant DB seeds its
    # own admin via provision_tenant_db). We MUST resolve the JWT user_id
    # against the tenant DB for the resolved subdomain -- never the control
    # DB -- otherwise auth either fails or matches a *different* tenant's user
    # that happens to share the same primary-key id (cross-tenant identity
    # leak). On the root host get_tenant_db transparently yields the control
    # DB, so single-tenant behaviour is unchanged.
    token = get_token_from_request(request)
    if not token:
        return None
    claims = decode_token_full(token)
    if not claims:
        return None
    user_id = claims.get("sub")
    if not user_id:
        return None

    # SECURITY: enforce tenant binding. A token minted for tenant A must not
    # authenticate against the root portal or tenant B. The token carries a
    # "tnt" claim (slug, or "" for root). We compare it to the tenant the
    # request resolved to via the Host header. Tokens issued before this
    # claim existed (no "tnt" key) are accepted only on the root portal to
    # avoid logging everyone out on deploy -- they expire within 24h anyway.
    req_tenant = getattr(request.state, "tenant", None)
    req_slug = req_tenant.slug if req_tenant else ""
    tok_slug = claims.get("tnt")
    if tok_slug is None:
        # Legacy token: only honour it on the root portal.
        if req_slug != "":
            return None
    elif tok_slug != req_slug:
        # Token was issued for a different tenant (or root) → reject.
        return None

    try:
        user = db.query(User).filter(User.id == int(user_id)).first()
    except (ValueError, TypeError):
        return None
    if not user or not user.is_active:
        return None
    return user


def get_current_user(
    user: Optional[User] = Depends(get_current_user_optional),
) -> User:
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user
