"""Production guardrail: assert_production_safe() must refuse weak secrets."""
from __future__ import annotations

import pytest


def test_forbidden_secrets_set_includes_known_placeholder():
    from app.core.config import _FORBIDDEN_SECRETS
    assert "change-this-secret-key-in-production" in _FORBIDDEN_SECRETS


def test_forbidden_admin_passwords_includes_admin123():
    from app.core.config import _FORBIDDEN_ADMIN_PASSWORDS
    for weak in ("admin123", "admin", "password", "123456", "changeme"):
        assert weak in _FORBIDDEN_ADMIN_PASSWORDS


def test_assert_production_safe_raises_on_weak_secret_in_production():
    """A placeholder SECRET_KEY is a HARD failure in every environment
    (a predictable key makes every JWT forgeable)."""
    from app.core.config import Settings

    s = Settings(
        SECRET_KEY="change-this-secret-key-in-production",
        DEFAULT_ADMIN_PASSWORD="admin123",
        ENV="production",
    )
    with pytest.raises(RuntimeError) as exc:
        s.assert_production_safe()
    msg = str(exc.value).lower()
    assert "secret_key" in msg


def test_assert_production_safe_raises_on_weak_admin_password_in_production(monkeypatch):
    """With a strong, non-placeholder SECRET_KEY, a weak DEFAULT_ADMIN_PASSWORD
    must still block startup in production (soft problem → raises in prod)."""
    from app.core.config import Settings

    # The pytest process runs with TESTING=true; unset it here so we exercise the
    # admin-password guard rather than the (also hard-failing) L-2 TESTING guard.
    monkeypatch.delenv("TESTING", raising=False)
    s = Settings(
        SECRET_KEY="x" * 48,                # strong, not a placeholder
        DEFAULT_ADMIN_PASSWORD="admin123",  # known-weak value
        ENV="production",
    )
    with pytest.raises(RuntimeError) as exc:
        s.assert_production_safe()
    assert "default_admin_password" in str(exc.value).lower()


def test_assert_production_safe_passes_with_strong_values(monkeypatch):
    from app.core.config import Settings

    monkeypatch.delenv("TESTING", raising=False)
    s = Settings(
        SECRET_KEY="a" * 48,                           # 48 chars, not placeholder
        DEFAULT_ADMIN_PASSWORD="SuperStrongPass!9000", # 20 chars, not in weak list
        ENV="production",
        # M-3: an explicit host allowlist without the "*" wildcard.
        ALLOWED_HOSTS="portal.example.com,www.example.com",
    )
    s.assert_production_safe()   # must not raise


def test_assert_production_safe_allows_empty_admin_password(monkeypatch):
    """AUDIT M-5: an EMPTY DEFAULT_ADMIN_PASSWORD is allowed (seeder skips the
    bootstrap admin). It must not be treated as a weak/short password."""
    from app.core.config import Settings

    monkeypatch.delenv("TESTING", raising=False)
    s = Settings(
        SECRET_KEY="a" * 48,
        DEFAULT_ADMIN_PASSWORD="",           # empty → skip-seed, not an error
        ENV="production",
        ALLOWED_HOSTS="portal.example.com",
    )
    s.assert_production_safe()   # must not raise


def test_assert_production_safe_raises_on_testing_flag_in_production(monkeypatch):
    """AUDIT L-2: TESTING=true must hard-fail in production."""
    from app.core.config import Settings

    monkeypatch.setenv("TESTING", "true")
    s = Settings(
        SECRET_KEY="a" * 48,
        DEFAULT_ADMIN_PASSWORD="SuperStrongPass!9000",
        ENV="production",
        ALLOWED_HOSTS="portal.example.com",
    )
    with pytest.raises(RuntimeError) as exc:
        s.assert_production_safe()
    assert "testing" in str(exc.value).lower()


def test_assert_production_safe_raises_on_wildcard_allowed_hosts(monkeypatch):
    """AUDIT M-3: a catch-all '*' in ALLOWED_HOSTS must hard-fail in production."""
    from app.core.config import Settings

    monkeypatch.delenv("TESTING", raising=False)
    s = Settings(
        SECRET_KEY="a" * 48,
        DEFAULT_ADMIN_PASSWORD="SuperStrongPass!9000",
        ENV="production",
        ALLOWED_HOSTS="portal.example.com,*",
    )
    with pytest.raises(RuntimeError) as exc:
        s.assert_production_safe()
    assert "allowed_hosts" in str(exc.value).lower()


def test_assert_production_safe_only_warns_in_development():
    """Soft problems (weak admin password) are warn-only in development so the
    dev server can boot without a fully hardened .env. A *valid* SECRET_KEY is
    required because a placeholder key is always a hard failure."""
    from app.core.config import Settings

    s = Settings(
        SECRET_KEY="x" * 48,                # valid key (placeholder would hard-fail)
        DEFAULT_ADMIN_PASSWORD="admin123",  # weak → soft problem, dev = warn only
        ENV="development",
    )
    s.assert_production_safe()   # development mode: warn-only, must not raise


def test_placeholder_secret_auto_repairs_in_development():
    """Audit 2026-07-07 Round 2: a placeholder SECRET_KEY in dev/staging is
    auto-repaired with an ephemeral random 64-char key instead of hard-failing.
    Production still hard-fails (covered by
    ``test_assert_production_safe_raises_on_weak_secret_in_production``)."""
    from app.core.config import Settings

    s = Settings(
        SECRET_KEY="change-this-secret-key-in-production",
        ENV="development",
    )
    s.assert_production_safe()   # must not raise in dev
    # After the call the placeholder was replaced with a strong ephemeral key.
    assert len(s.SECRET_KEY) == 64
    assert s.SECRET_KEY != "change-this-secret-key-in-production"


def test_placeholder_secret_still_hard_fails_in_production():
    """Regression guard: prod must still refuse the placeholder."""
    from app.core.config import Settings

    s = Settings(
        SECRET_KEY="change-this-secret-key-in-production",
        ENV="production",
    )
    with pytest.raises(RuntimeError):
        s.assert_production_safe()


def test_is_production_property():
    from app.core.config import Settings
    assert Settings(ENV="production").is_production is True
    assert Settings(ENV="development").is_production is False
    assert Settings(ENV="").is_production is False
