"""Security boundary regressions for the multi-tenant portal."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.db.session import SessionLocal
from app.models import User
from app.models.lookups import PasswordResetToken


def test_tenant_local_admin_cannot_enter_control_plane():
    from app.api.endpoints.tenants import _require_control_plane_super_admin

    request = SimpleNamespace(state=SimpleNamespace(tenant=SimpleNamespace(slug="alpha")))
    tenant_admin = SimpleNamespace(is_super_admin=True)

    with pytest.raises(HTTPException) as exc:
        _require_control_plane_super_admin(request, tenant_admin)
    assert exc.value.status_code == 403


def test_only_admin_role_can_hold_wildcard_permission():
    from app.core.permissions import validate_role_permissions

    with pytest.raises(ValueError):
        validate_role_permissions("manager", ["*"])
    with pytest.raises(ValueError):
        validate_role_permissions("custom-ops", ["*"])
    assert validate_role_permissions("admin", ["*"], is_system=True) == ["*"]


def test_role_names_cannot_impersonate_admin():
    from app.core.permissions import validate_role_name

    with pytest.raises(ValueError):
        validate_role_name("Admin")
    assert validate_role_name("operations") == "operations"


def test_password_policy_is_shared_and_rejects_weak_passwords():
    from app.core.security import validate_password_strength

    for password in ("short12", "admin123", "password123", "123456789012"):
        with pytest.raises(ValueError):
            validate_password_strength(password, current_password="OldStrongPass123!")

    validate_password_strength("NewStrongPass123!", current_password="OldStrongPass123!")


def test_reset_token_claim_is_atomic_and_replay_is_rejected():
    from app.services.password_reset import claim_reset_token

    db = SessionLocal()
    email = f"reset-{uuid4().hex}@example.test"
    user = User(name="Reset User", email=email, password_hash="unused", role="staff")
    db.add(user)
    db.commit()
    db.refresh(user)
    token = PasswordResetToken(
        user_id=user.id,
        email=email,
        token_hash=uuid4().hex,
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    db.add(token)
    db.commit()
    db.refresh(token)

    assert claim_reset_token(db, token.token_hash) is True
    db.commit()
    assert claim_reset_token(db, token.token_hash) is False
    db.rollback()
    db.delete(token)
    db.delete(user)
    db.commit()
    db.close()


def test_password_change_revokes_other_reset_tokens():
    from app.services.password_reset import revoke_unused_reset_tokens

    db = SessionLocal()
    email = f"revoke-{uuid4().hex}@example.test"
    user = User(name="Revoke User", email=email, password_hash="unused", role="staff")
    db.add(user)
    db.commit()
    db.refresh(user)
    now = datetime.now(UTC)
    rows = [
        PasswordResetToken(user_id=user.id, email=email, token_hash=uuid4().hex,
                           expires_at=now + timedelta(minutes=30)),
        PasswordResetToken(user_id=user.id, email=email, token_hash=uuid4().hex,
                           expires_at=now + timedelta(minutes=30)),
    ]
    db.add_all(rows)
    db.commit()

    assert revoke_unused_reset_tokens(db, user.id) == 2
    db.commit()
    assert all(row.used_at is not None for row in db.query(PasswordResetToken)
               .filter(PasswordResetToken.user_id == user.id).all())
    db.query(PasswordResetToken).filter(PasswordResetToken.user_id == user.id).delete()
    db.delete(user)
    db.commit()
    db.close()
