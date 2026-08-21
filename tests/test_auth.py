"""Auth: login, must_change_password flag, change-password flow."""
from __future__ import annotations


def test_login_success_returns_token_and_must_change_flag(client, admin_credentials):
    r = client.post(
        "/api/auth/login",
        json={
            "username": admin_credentials["username"],
            "password": admin_credentials["password"],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("access_token"), "missing access_token"
    user = body.get("user") or {}
    # Bootstrap admin MUST be flagged for forced password change
    assert user.get("must_change_password") is True, \
        f"bootstrap admin should be must_change_password=True, got {user}"
    assert user.get("email") == admin_credentials["email"]
    assert user.get("role") == "admin"


def test_login_invalid_password_rejected(client, admin_credentials):
    r = client.post(
        "/api/auth/login",
        json={"username": admin_credentials["username"], "password": "wrong-pw"},
    )
    assert r.status_code == 401


def test_login_missing_identifier_rejected(client):
    r = client.post("/api/auth/login", json={"password": "whatever"})
    assert r.status_code in (400, 422)


def test_change_password_requires_auth(client):
    r = client.post(
        "/api/auth/change-password",
        json={"current_password": "x", "new_password": "y"},
    )
    assert r.status_code == 401


def test_change_password_wrong_current_rejected(client, auth_headers, admin_credentials):
    r = client.post(
        "/api/auth/change-password",
        headers=auth_headers,
        json={
            "current_password": "definitely-not-correct",
            "new_password":     "BrandNewStrongPass123!",
        },
    )
    assert r.status_code == 400
    assert "current password" in r.json().get("detail", "").lower()


def test_change_password_too_short_rejected(client, auth_headers, admin_credentials):
    r = client.post(
        "/api/auth/change-password",
        headers=auth_headers,
        json={
            "current_password": admin_credentials["password"],
            "new_password":     "short",
        },
    )
    assert r.status_code == 400
    assert "12 characters" in r.json().get("detail", "")


def test_change_password_weak_listed_rejected(client, auth_headers, admin_credentials):
    r = client.post(
        "/api/auth/change-password",
        headers=auth_headers,
        json={
            "current_password": admin_credentials["password"],
            "new_password":     "admin123",       # in weak list
        },
    )
    assert r.status_code == 400


def test_change_password_full_flow_clears_must_change_flag(client, auth_headers, admin_credentials):
    """End-to-end: change PW, re-login, verify must_change_password=False."""
    new_pw = "ProductionGradePass!2026"

    # 1. change it
    r = client.post(
        "/api/auth/change-password",
        headers=auth_headers,
        json={
            "current_password": admin_credentials["password"],
            "new_password":     new_pw,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json().get("ok") is True

    # 2. old PW should now fail
    r2 = client.post(
        "/api/auth/login",
        json={"username": admin_credentials["username"],
              "password": admin_credentials["password"]},
    )
    assert r2.status_code == 401

    # 3. new PW should succeed AND must_change_password should be False
    r3 = client.post(
        "/api/auth/login",
        json={"username": admin_credentials["username"], "password": new_pw},
    )
    assert r3.status_code == 200, r3.text
    assert r3.json()["user"]["must_change_password"] is False

    # 4. restore original PW so subsequent tests using `admin_token` keep working
    new_token = r3.json()["access_token"]
    r4 = client.post(
        "/api/auth/change-password",
        headers={"Authorization": f"Bearer {new_token}"},
        json={
            "current_password": new_pw,
            "new_password":     admin_credentials["password"],
        },
    )
    assert r4.status_code == 200


# ---------------------------------------------------------------------------
# Correction-#2: password-reset must NOT report false success when the server
# cannot actually send email. In the test environment SMTP_HOST/SMTP_USER are
# empty (see app/core/config.py defaults), so forgot-password must hard-fail
# with 503 instead of pretending a reset link was sent.
# ---------------------------------------------------------------------------
def test_forgot_password_503_when_smtp_unconfigured(client, monkeypatch):
    # Force SMTP to be unconfigured regardless of what .env happens to contain.
    from app.core.config import settings as _settings
    monkeypatch.setattr(_settings, "SMTP_HOST", "", raising=False)
    monkeypatch.setattr(_settings, "SMTP_USER", "", raising=False)

    r = client.post(
        "/api/auth/forgot-password",
        json={"email": "admin@yourcompany.com"},
    )
    assert r.status_code == 503, r.text
    detail = r.json().get("detail", "").lower()
    assert "email delivery" in detail or "not configured" in detail


def test_forgot_password_no_enumeration_unconfigured(client, monkeypatch):
    """Existent and non-existent emails must behave IDENTICALLY (503 both) so
    the config-first check can never be used to enumerate accounts."""
    from app.core.config import settings as _settings
    monkeypatch.setattr(_settings, "SMTP_HOST", "", raising=False)
    monkeypatch.setattr(_settings, "SMTP_USER", "", raising=False)

    r_existing = client.post(
        "/api/auth/forgot-password", json={"email": "admin@yourcompany.com"}
    )
    r_missing = client.post(
        "/api/auth/forgot-password", json={"email": "nobody-here@example.com"}
    )
    assert r_existing.status_code == r_missing.status_code == 503
    assert r_existing.json().get("detail") == r_missing.json().get("detail")


def test_forgot_password_missing_email_rejected(client):
    r = client.post("/api/auth/forgot-password", json={})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Account-enumeration regression (2026-07-26). The tests above only cover the
# SMTP-*unconfigured* path. The leak lived in the configured-but-BROKEN path:
# the endpoint used to raise 500 when the SMTP send threw, and that code is
# reachable only after matching an active user, so the status code alone
# disclosed whether an address was registered:
#
#     registered   -> send attempted -> raises -> 500
#     unregistered -> early return            -> 200
#
# This is very easy to hit in practice because .env.example ships placeholder
# SMTP values that are truthy enough to pass the config gate but cannot
# authenticate to any mail host.
#
# NOTE these tests deliberately use the REAL seeded admin address from the
# fixtures. The older tests above hardcode "admin@yourcompany.com", which is not
# the seeded account in the test environment (conftest sets admin@test.local),
# so they were in fact comparing two *non-existent* addresses and could not have
# caught this class of bug.
# ---------------------------------------------------------------------------
def _force_broken_smtp(monkeypatch):
    """Make SMTP look configured but fail on send."""
    from app.core.config import settings as _settings
    monkeypatch.setattr(_settings, "SMTP_HOST", "smtp.real-looking-host.test", raising=False)
    monkeypatch.setattr(_settings, "SMTP_USER", "mailer@real-looking-host.test", raising=False)
    monkeypatch.setattr(_settings, "SMTP_PASSWORD", "irrelevant", raising=False)

    import smtplib

    def _boom(*a, **kw):
        raise smtplib.SMTPAuthenticationError(535, b"auth failed")

    monkeypatch.setattr(smtplib, "SMTP", _boom)


def test_forgot_password_no_enumeration_when_smtp_send_fails(
    client, monkeypatch, admin_credentials
):
    """A broken mail server must not turn the endpoint into an account oracle."""
    _force_broken_smtp(monkeypatch)

    r_existing = client.post(
        "/api/auth/forgot-password", json={"email": admin_credentials["email"]}
    )
    r_missing = client.post(
        "/api/auth/forgot-password", json={"email": "nobody-here-at-all@example.com"}
    )

    assert r_existing.status_code == r_missing.status_code, (
        "Registered and unregistered addresses returned DIFFERENT status codes "
        f"({r_existing.status_code} vs {r_missing.status_code}) when SMTP "
        "delivery failed. The status code is now an account-existence oracle."
    )
    assert r_existing.json() == r_missing.json(), (
        "Response bodies differ between a registered and an unregistered "
        f"address: {r_existing.json()!r} vs {r_missing.json()!r}"
    )
    # And the shared response must be the account-neutral success, not a 5xx.
    assert r_existing.status_code == 200, r_existing.text


def test_forgot_password_placeholder_smtp_treated_as_unconfigured(client, monkeypatch):
    """.env.example placeholders must not count as working email config.

    Otherwise the honest 503 is skipped and the request instead dies inside the
    send with a confusing error.
    """
    from app.core.config import settings as _settings
    monkeypatch.setattr(_settings, "SMTP_HOST", "smtp.yourprovider.com", raising=False)
    monkeypatch.setattr(_settings, "SMTP_USER", "noreply@yourcompany.com", raising=False)

    assert _settings.email_configured is False

    r = client.post(
        "/api/auth/forgot-password", json={"email": "anyone@example.com"}
    )
    assert r.status_code == 503, r.text


def test_email_configured_property():
    """The single source of truth for 'can this server send mail?'."""
    from app.core.config import Settings

    assert Settings(SMTP_HOST="", SMTP_USER="").email_configured is False
    assert Settings(SMTP_HOST="smtp.x.io", SMTP_USER="").email_configured is False
    assert Settings(SMTP_HOST="", SMTP_USER="a@x.io").email_configured is False
    # Whitespace-only must not count as configured either.
    assert Settings(SMTP_HOST="   ", SMTP_USER="  ").email_configured is False
    # Placeholders from .env.example (case-insensitive).
    assert Settings(
        SMTP_HOST="smtp.yourprovider.com", SMTP_USER="noreply@yourcompany.com"
    ).email_configured is False
    assert Settings(
        SMTP_HOST="SMTP.YourProvider.COM", SMTP_USER="NoReply@YourCompany.com"
    ).email_configured is False
    # A genuine-looking pair is accepted.
    assert Settings(
        SMTP_HOST="smtp.sendgrid.net", SMTP_USER="apikey"
    ).email_configured is True


def test_health_reports_password_reset_capability(client, monkeypatch):
    """Operators should learn about this from monitoring, not from a stuck user."""
    from app.core.config import settings as _settings

    monkeypatch.setattr(_settings, "SMTP_HOST", "", raising=False)
    monkeypatch.setattr(_settings, "SMTP_USER", "", raising=False)
    body = client.get("/api/health").json()
    assert body["password_reset"] == "unavailable_smtp_not_configured"

    monkeypatch.setattr(_settings, "SMTP_HOST", "smtp.sendgrid.net", raising=False)
    monkeypatch.setattr(_settings, "SMTP_USER", "apikey", raising=False)
    r = client.get("/api/health")
    assert r.json()["password_reset"] == "available"
    # An optional feature being unavailable must never mark the app unhealthy.
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Correction-#2: CSRF must NOT exempt the whole /api/auth/ prefix. Only
# login / forgot-password / reset-password are exempt; state-changing auth
# endpoints (change-password, logout, support-message) must be protected.
# (The middleware short-circuits when TESTING=true, so we assert the exemption
# configuration directly — that is the actual behaviour change.)
# ---------------------------------------------------------------------------
def test_csrf_exemption_list_is_precise():
    from app.main import _CSRF_EXEMPT_PATHS, _CSRF_EXEMPT_PREFIXES

    def _is_exempt(p: str) -> bool:
        return p in _CSRF_EXEMPT_PATHS or any(
            p.startswith(pref) for pref in _CSRF_EXEMPT_PREFIXES
        )

    # Exempt: unauthenticated / bootstrap endpoints
    assert _is_exempt("/api/auth/login")
    assert _is_exempt("/api/auth/forgot-password")
    assert _is_exempt("/api/auth/reset-password")

    # NOT exempt: state-changing authenticated endpoints
    assert not _is_exempt("/api/auth/change-password")
    assert not _is_exempt("/api/auth/logout")
    assert not _is_exempt("/api/auth/support-message")

    # The dangerous blanket prefix must be gone
    assert "/api/auth/" not in _CSRF_EXEMPT_PREFIXES


def test_seed_admin_does_not_rewrite_an_existing_password(tmp_path, monkeypatch):
    """`seed_admin_user` must NEVER silently re-hash an existing admin row.

    WHY THIS TEST EXISTS
    --------------------
    A real support incident: the portal login screen rejected the password
    written in `.env` with "Invalid username or password". The cause was not a
    bug in the auth code at all -- `seed_admin_user()` returns early when the
    admin row already exists (see app/db/init_db.py), so editing
    DEFAULT_ADMIN_PASSWORD in `.env` after the first boot changes NOTHING in
    the database. `.env` and the stored hash drift apart, and the operator is
    locked out with a message that points at the wrong thing.

    The early return is deliberate and CORRECT -- rewriting the password on
    every boot would silently undo any password an admin had chosen in the UI,
    and would resurrect the `.env` value forever. So this test pins the
    *intended* behaviour rather than "fixing" it, and its docstring is the
    breadcrumb for the next person who hits the lockout. The supported recovery
    path is `scripts/reset_admin_password.py`, covered by the assertion below.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.core.security import hash_password, verify_password
    from app.db.init_db import seed_admin_user
    from app.db.session import Base
    from app.models.user import User

    # A throwaway database containing only what this test touches, so the
    # assertion cannot be influenced by the shared fixture's seeded admin.
    engine = create_engine(f"sqlite:///{tmp_path / 'seed_drift.db'}")
    Base.metadata.create_all(bind=engine, tables=[User.__table__])
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        original = "OriginalPass_1!"
        db.add(
            User(
                name="Administrator",
                email="drift-admin@example.com",
                password_hash=hash_password(original),
                role="admin",
                is_active=True,
            )
        )
        db.commit()

        monkeypatch.setattr(
            "app.db.init_db.settings.DEFAULT_ADMIN_EMAIL",
            "drift-admin@example.com",
            raising=False,
        )
        monkeypatch.setattr(
            "app.db.init_db.settings.DEFAULT_ADMIN_PASSWORD",
            "A_Totally_Different_Pass_9!",
            raising=False,
        )

        seed_admin_user(db)

        row = db.query(User).filter(User.email == "drift-admin@example.com").one()
        assert verify_password(original, row.password_hash), (
            "seed_admin_user overwrote an existing admin password -- that would "
            "clobber a password the admin chose in the UI"
        )
        assert not verify_password("A_Totally_Different_Pass_9!", row.password_hash), (
            "the .env password must NOT take effect on an existing row; if this "
            "starts passing, update scripts/reset_admin_password.py's docs"
        )
    finally:
        db.close()
