import logging
import secrets
from datetime import UTC, datetime, timedelta
from html import escape as html_escape

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    create_access_token,
    hash_password,
    validate_password_strength,
    verify_password,
)
from app.core.tenancy import get_tenant_db
from app.models import User
from app.models.lookups import LoginHistory, PasswordResetToken, SupportMessage
from app.schemas.schemas import LoginRequest, TokenResponse
from app.services.password_reset import claim_reset_token, revoke_unused_reset_tokens

router = APIRouter()
log = logging.getLogger("dtc.auth")

import hashlib
import os

from app.core.limiter import limiter


def _client_ip(request: Request) -> str:
    """Best-effort client IP used for brute-force login throttling.

    SECURITY (audit 2026-07-07): the previous implementation trusted the
    *leftmost* ``X-Forwarded-For`` entry, which is entirely attacker-controlled
    -- a client can send ``X-Forwarded-For: 1.2.3.4`` and get a fresh lockout
    bucket on every request, completely defeating the IP throttle.

    We now derive the IP from ``settings.TRUSTED_PROXY_COUNT``:

      * ``0`` (default, safe for direct exposure / a proxy that does NOT
        forward a trustworthy XFF): ignore the header entirely and use the real
        socket peer (``request.client.host``). Not spoofable.

      * ``N >= 1``: the app sits behind N trusted proxies that each APPEND the
        peer they saw. The trustworthy address is therefore the Nth entry from
        the right of the XFF list (everything to the left of it was supplied by
        the untrusted client and must not be trusted). If the header is missing
        or too short, we fall back to the socket peer.
    """
    peer = request.client.host if request.client else ""
    proxies = getattr(settings, "TRUSTED_PROXY_COUNT", 0) or 0
    if proxies < 1:
        # No trusted proxy -> XFF is untrustworthy. Use the socket peer.
        return peer
    fwd = request.headers.get("x-forwarded-for")
    if not fwd:
        return peer
    chain = [p.strip() for p in fwd.split(",") if p.strip()]
    if not chain:
        return peer
    # Take the Nth hop from the right (the address our own trusted proxy saw).
    idx = len(chain) - proxies
    if idx < 0:
        # XFF shorter than the configured proxy count -> chain was likely
        # truncated/forged; fall back to the leftmost real entry we have.
        idx = 0
    return chain[idx] or peer


# Brute-force throttle: too many failed logins from one IP within the window
# results in a temporary 429 lockout.
_MAX_FAILED_ATTEMPTS = 8
_LOCKOUT_WINDOW_MINUTES = 10


def _too_many_failures(db: Session, ip: str) -> bool:
    # Disable brute-force lockout during automated tests
    if os.environ.get("TESTING") == "true":
        return False
    if not ip:
        return False
    since = datetime.now(UTC) - timedelta(minutes=_LOCKOUT_WINDOW_MINUTES)
    try:
        recent = (
            db.query(LoginHistory)
            .filter(
                LoginHistory.ip_address == ip,
                LoginHistory.status == "Failed",
                LoginHistory.occurred_at >= since,
            )
            .count()
        )
    except Exception as exc:  # noqa: BLE001 -- see the fail-open note below
        # AUDIT 2026-07-31 (Medium): this handler used to `return False`
        # silently. Returning False means "this IP is not locked out", so ANY
        # database hiccup here quietly switches brute-force protection OFF with
        # no trace whatsoever in the logs.
        #
        # Failing OPEN is still the right call -- a transient DB error must not
        # lock every legitimate user out of the portal -- but it MUST be loud,
        # because a persistent failure here is indistinguishable from having no
        # brute-force protection at all.
        log.warning(
            "brute-force lockout check FAILED for ip=%s -- failing OPEN "
            "(login throttling is not being enforced for this request): %s",
            ip, exc,
        )
        return False
    return recent >= _MAX_FAILED_ATTEMPTS


@router.post("/login", response_model=TokenResponse, response_model_exclude_none=True)
@limiter.limit("10/minute")
def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_tenant_db)):
    ip = _client_ip(request)
    ua = request.headers.get("user-agent", "")[:300]

    if _too_many_failures(db, ip):
        raise HTTPException(
            status_code=429,
            detail="Too many failed attempts. Please try again later.",
        )

    # Resolve the login identifier -- username takes priority, email is the fallback
    identifier = (payload.username or "").strip() or (payload.email or "").strip()
    if not identifier:
        raise HTTPException(status_code=400, detail="Username or email is required")

    # Try username first, then email
    user = (
        db.query(User).filter(User.username == identifier).first()
        or db.query(User).filter(User.email == identifier).first()
    )

    def record(status: str):
        db.add(LoginHistory(email=identifier, status=status, ip_address=ip, user_agent=ua))
        db.commit()

    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        record("Failed")
        raise HTTPException(status_code=401, detail="Invalid username or password")

    record("Success")
    # Bind the token to the tenant it was issued for so it cannot be replayed
    # against the root portal or a different tenant (where the same numeric
    # user id belongs to a different person). Root portal → "" (empty slug).
    _t = getattr(request.state, "tenant", None)
    token = create_access_token(subject=user.id, tenant=(_t.slug if _t else ""))
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.is_production,   # True in production (HTTPS required); False in dev (HTTP ok)
        max_age=60 * settings.ACCESS_TOKEN_EXPIRE_MINUTES,
    )
    # The frontend uses `must_change_password` to redirect the user to a
    # forced password-change screen before the rest of the app is
    # accessible. This is set to True for the bootstrap admin so the
    # documented default credentials cannot survive past first login.
    # SECURITY: Do not return the JWT to browser JavaScript. The secure,
    # HttpOnly cookie above is the single browser credential. We only expose the
    # raw token during automated tests so existing API-auth regression tests can
    # keep exercising Authorization: Bearer flows without weakening production.
    return TokenResponse(
        access_token=token if os.environ.get("TESTING") == "true" else None,
        user={
            "id": user.id, "name": user.name, "email": user.email,
            "role": user.role,
            "must_change_password": bool(getattr(user, "must_change_password", False)),
        },
    )


from app.core.deps import get_current_user  # noqa: E402 -- used by /change-password


@router.post("/change-password")
def change_password(
    payload: dict,
    db: Session = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    """Authenticated password change.

    Used by:
      1. The forced first-login flow (bootstrap admin)
      2. Any user who wants to rotate their own password

    Requires the current password for verification + a new password
    that satisfies the minimum strength rules.
    """
    current = payload.get("current_password") or ""
    new_pw  = payload.get("new_password") or ""

    if not verify_password(current, user.password_hash):
        raise HTTPException(400, "Current password is incorrect")

    try:
        validate_password_strength(new_pw, current)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    user.password_hash = hash_password(new_pw)
    user.must_change_password = False
    revoke_unused_reset_tokens(db, user.id)
    db.commit()
    return {"ok": True, "message": "Password updated successfully"}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie("access_token", samesite="lax", secure=settings.is_production)
    return {"ok": True}


@router.post("/forgot-password")
@limiter.limit("5/minute")
def forgot_password(payload: dict, request: Request, db: Session = Depends(get_tenant_db)):
    """Generate a one-time reset token.

    H-02: Token is NO longer returned in the response body (it must be delivered
    via email to the registered address). In this deployment, integrate an SMTP
    sender and replace the debug log below with an actual email send.

    H-03: Rate-limited to the same IP lockout used by /login -- prevents account
    enumeration, reset-link spam, and PasswordResetToken row-flooding.
    """
    ip = _client_ip(request)

    # H-03: Apply IP-based rate limit (reuses the login failure counter)
    if _too_many_failures(db, ip):
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please try again later.",
        )

    email = (payload.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(400, "Email is required")

    # Config check FIRST, before any user lookup. Whether email delivery is
    # configured is a server-wide fact that does NOT depend on the address, so
    # answering it up-front cannot leak account existence. Doing it here means
    # existent and non-existent emails behave identically (no enumeration) while
    # still refusing to pretend a reset "was sent" on a server that can't send.
    # settings.email_configured also rejects the .env.example placeholder values,
    # which are truthy but cannot authenticate — without that check we would skip
    # this honest 503 and instead fail later inside the send. (2026-07-26)
    if not settings.email_configured:
        import logging
        logging.getLogger("dtc.auth").warning(
            "Password reset requested but SMTP is not configured — "
            "set SMTP_HOST/SMTP_USER/SMTP_PASSWORD to enable delivery."
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "Password reset email cannot be sent because email delivery "
                "is not configured on the server. Please contact your "
                "administrator."
            ),
        )

    user = db.query(User).filter(User.email == email).first()
    # Always return the same message -- do NOT reveal whether the address is registered
    if not user or not user.is_active:
        return {"ok": True, "message": "If an account exists for that email, a reset link has been sent."}

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    pr = PasswordResetToken(
        user_id=user.id, email=user.email, token_hash=token_hash,
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    db.add(pr); db.commit()
    # The raw token is used in the email URL, not stored in DB
    token = raw_token

    # FIX-10: Attempt real email delivery if SMTP is configured in settings.
    # Falls back to debug-only logging when SMTP is not set up (dev/test).
    import logging
    _log = logging.getLogger("dtc.auth")
    _sent = False
    _smtp_configured = settings.email_configured
    _send_attempt_failed = False
    if _smtp_configured:
        try:
            import smtplib
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText
            # Build absolute URL: use configured APP_BASE_URL (for proxy setups) or
            # derive from the incoming request so the email link is always absolute.
            base_url = (settings.APP_BASE_URL or str(request.base_url)).rstrip("/")
            reset_url = f"{base_url}/reset-password?token={token}"
            msg = MIMEMultipart("alternative")
            msg["Subject"] = "Password Reset — Dogar Trading Corporation Portal"
            msg["From"] = settings.SMTP_FROM or settings.SMTP_USER
            msg["To"] = user.email
            body_text = (
                f"You requested a password reset.\n\n"
                f"Use this link within 30 minutes:\n{reset_url}\n\n"
                f"If you did not request this, ignore this email."
            )
            safe_reset_url = html_escape(reset_url, quote=True)
            body_html = (
                f"<p>You requested a password reset.</p>"
                f"<p><a href=\"{safe_reset_url}\">Click here to reset your password</a></p>"
                f"<p>This link expires in 30 minutes. If you did not request this, ignore this email.</p>"
            )
            msg.attach(MIMEText(body_text, "plain"))
            msg.attach(MIMEText(body_html, "html"))
            smtp_cls = smtplib.SMTP
            with smtp_cls(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as srv:
                if settings.SMTP_TLS:
                    srv.starttls()
                srv.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                srv.sendmail(msg["From"], [user.email], msg.as_string())
            _sent = True
            _log.info("Password reset email sent to %s", email)
        except Exception as smtp_exc:
            _send_attempt_failed = True
            _log.error("SMTP send failed for %s: %s", email, smtp_exc)
    if not _sent:
        # H-06 FIX (audit 2026-07-03): the raw reset token must NEVER be
        # written to any log stream at ANY level. DEBUG logs are routinely
        # captured by log aggregators / Sentry breadcrumbs in production,
        # which would leak a live account-takeover token. We only record
        # that delivery failed — the user must retry once SMTP is fixed.
        _log.debug("Password reset email could not be delivered for %s "
                   "(token withheld from logs by policy)", email)
        if not settings.email_configured:
            _log.warning(
                "SMTP not configured — password reset link for %s was NOT emailed. "
                "Set SMTP_HOST/SMTP_USER/SMTP_PASSWORD in .env to enable email delivery.",
                email
            )

    # H-02: Do NOT include the token or reset_url in the API response, and keep
    # the public message account-neutral (anti-enumeration).
    #
    # ACCOUNT ENUMERATION FIX (2026-07-26). The previous version raised 500 here
    # when the SMTP send threw. That reintroduced exactly the enumeration leak
    # the rest of this function works so hard to avoid, because this code is
    # only reachable AFTER matching an active user:
    #
    #     registered address    -> send attempted -> throws -> 500
    #     unregistered address  -> early return above       -> 200
    #
    # So with reachable-but-broken SMTP (very common: the shipped .env.example
    # carries placeholder SMTP_HOST/SMTP_USER values, which are truthy enough to
    # pass the config check at the top but cannot authenticate), the HTTP status
    # alone was a reliable "is this email registered?" oracle — no timing
    # analysis needed. Verified against a live instance before this fix:
    # existing user -> 500, unknown user -> 200.
    #
    # A delivery failure is a SERVER-side fault that the requester can neither
    # cause nor fix, and it says nothing about the address they typed. So it
    # must not change the response they see. We return the same account-neutral
    # 200 as every other branch and escalate loudly on the server side instead,
    # where the operator will actually act on it:
    #
    #   * ERROR log (already emitted at the except site above)
    #   * GET /api/health reports password_reset availability
    #   * the boot-time WARNING in Settings.assert_production_safe()
    #
    # The token row stays in the DB and simply expires unused after 30 minutes;
    # it is single-use and hash-stored, so an undelivered token is inert.
    if _send_attempt_failed:
        _log.error(
            "Password reset for %s could not be delivered (SMTP send failed). "
            "Returning the standard account-neutral response to avoid account "
            "enumeration — the user cannot complete a reset until SMTP is "
            "fixed. Check SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASSWORD.",
            email,
        )
    return {
        "ok": True,
        "message": "If an account exists for that email, a reset link has been sent.",
    }


@router.post("/reset-password")
def reset_password(payload: dict, db: Session = Depends(get_tenant_db)):
    token = (payload.get("token") or "").strip()
    new_password = payload.get("new_password") or ""
    if not token or not new_password:
        raise HTTPException(400, "Token and new password are required")
    try:
        validate_password_strength(new_password)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    token_hash = hashlib.sha256(token.encode()).hexdigest()
    # Claim before reading/updating the user. The conditional UPDATE is atomic,
    # so two concurrent redemptions cannot both pass the old check-then-set
    # sequence.
    if not claim_reset_token(db, token_hash):
        existing = db.query(PasswordResetToken).filter(
            PasswordResetToken.token_hash == token_hash
        ).first()
        if not existing:
            raise HTTPException(400, "Invalid or expired token")
        if existing.used_at:
            raise HTTPException(400, "This reset link has already been used")
        raise HTTPException(400, "This reset link has expired")

    pr = db.query(PasswordResetToken).filter(PasswordResetToken.token_hash == token_hash).first()
    if not pr:
        db.rollback()
        raise HTTPException(400, "Invalid or expired token")

    user = db.query(User).filter(User.id == pr.user_id).first()
    if not user:
        db.rollback()
        raise HTTPException(400, "User not found")

    user.password_hash = hash_password(new_password)
    revoke_unused_reset_tokens(db, user.id, exclude_id=pr.id)
    db.commit()
    return {"ok": True, "message": "Password updated successfully. You can now sign in."}


@router.post("/support-message")
def submit_support_message(payload: dict, db: Session = Depends(get_tenant_db)):
    """Submit a message to HussnainTechVertex support team."""
    msg = SupportMessage(
        name=(payload.get("name") or "").strip()[:150],
        email=(payload.get("email") or "").strip()[:150],
        phone=(payload.get("phone") or "").strip()[:50],
        subject=(payload.get("subject") or "").strip()[:255],
        message=(payload.get("message") or "").strip(),
        status="open",
    )
    if not msg.email or not msg.message:
        raise HTTPException(400, "Email and message are required")
    db.add(msg); db.commit(); db.refresh(msg)
    return {"ok": True, "id": msg.id, "message": "Your message has been received. Our team will contact you within 24 hours."}
