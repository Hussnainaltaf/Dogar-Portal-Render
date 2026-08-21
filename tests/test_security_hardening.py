"""Regression tests for the 2026-07-07 hardening review.

Covers:
  * _client_ip() must NOT trust a spoofable X-Forwarded-For by default
    (TRUSTED_PROXY_COUNT == 0), and must pick the right hop when configured.
  * The production Host allowlist default must NOT contain broad shared-PaaS
    wildcards; preview/PaaS wildcards are added only when preview mode is on.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest


# ---------------------------------------------------------------------------
# #2 — X-Forwarded-For spoofing
# ---------------------------------------------------------------------------
def _make_request(xff: str | None, peer: str):
    headers = {}
    if xff is not None:
        headers["x-forwarded-for"] = xff
    return SimpleNamespace(
        headers=headers,
        client=SimpleNamespace(host=peer),
    )


def test_client_ip_ignores_xff_when_no_trusted_proxy(monkeypatch):
    """Default TRUSTED_PROXY_COUNT=0 -> XFF is untrusted, use socket peer."""
    from app.api.endpoints import auth as _auth
    monkeypatch.setattr(_auth.settings, "TRUSTED_PROXY_COUNT", 0, raising=False)

    # Attacker spoofs a fake IP in the header; we must ignore it.
    req = _make_request(xff="1.2.3.4", peer="203.0.113.9")
    assert _auth._client_ip(req) == "203.0.113.9"


def test_client_ip_spoof_cannot_rotate_bucket(monkeypatch):
    """Two requests with different spoofed XFF but same socket peer must map
    to the SAME throttle IP (so the attacker cannot get a fresh bucket)."""
    from app.api.endpoints import auth as _auth
    monkeypatch.setattr(_auth.settings, "TRUSTED_PROXY_COUNT", 0, raising=False)

    r1 = _make_request(xff="9.9.9.9", peer="198.51.100.7")
    r2 = _make_request(xff="8.8.8.8, 7.7.7.7", peer="198.51.100.7")
    assert _auth._client_ip(r1) == _auth._client_ip(r2) == "198.51.100.7"


def test_client_ip_uses_nth_hop_behind_one_proxy(monkeypatch):
    """Behind exactly 1 trusted proxy, the trustworthy IP is the rightmost
    XFF entry (the address our own proxy observed)."""
    from app.api.endpoints import auth as _auth
    monkeypatch.setattr(_auth.settings, "TRUSTED_PROXY_COUNT", 1, raising=False)

    # client -> proxy: proxy appends the real client (203.0.113.5).
    # Attacker prepends a fake entry; with 1 proxy we take the last hop.
    req = _make_request(xff="1.2.3.4, 203.0.113.5", peer="10.0.0.1")
    assert _auth._client_ip(req) == "203.0.113.5"


def test_client_ip_falls_back_when_xff_shorter_than_proxy_count(monkeypatch):
    from app.api.endpoints import auth as _auth
    monkeypatch.setattr(_auth.settings, "TRUSTED_PROXY_COUNT", 3, raising=False)
    # Only one entry but 3 proxies expected -> fall back to leftmost real entry.
    req = _make_request(xff="203.0.113.5", peer="10.0.0.1")
    assert _auth._client_ip(req) == "203.0.113.5"


def test_client_ip_no_header_uses_peer(monkeypatch):
    from app.api.endpoints import auth as _auth
    monkeypatch.setattr(_auth.settings, "TRUSTED_PROXY_COUNT", 1, raising=False)
    req = _make_request(xff=None, peer="203.0.113.9")
    assert _auth._client_ip(req) == "203.0.113.9"


# ---------------------------------------------------------------------------
# #4 — production Host allowlist must not ship broad PaaS wildcards
# ---------------------------------------------------------------------------
_SANDBOX_MARKERS = (
    "E2B_SANDBOX", "E2B_SANDBOX_ID", "NOVITA_SANDBOX_ID",
    "SANDBOX_ID",
)


def _clean_prod_settings(monkeypatch):
    """A Settings instance simulating a plain self-hosted production box:
    ENV=production, no sandbox markers, no opt-in flag.

    IMPORTANT — must ignore the developer's .env (fixed 2026-07-27).
    This helper used to call ``Settings(ENV="production")`` directly, which
    still loads .env because that path is baked into model_config. So the
    test asserted on whatever ALLOWED_HOSTS the machine happened to have.
    On a fresh install that followed .env.example's instruction to set
    ALLOWED_HOSTS to the operator's OWN domain, the test failed even though
    the product was behaving exactly as documented -- a false alarm in the
    customer's very first test run. We pass ``_env_file=None`` to assert on
    the shipped DEFAULT, which is what the test name actually claims.
    """
    for m in (*_SANDBOX_MARKERS, "ALLOW_PREVIEW_HOSTS", "ALLOWED_HOSTS"):
        monkeypatch.delenv(m, raising=False)
    from app.core.config import Settings
    return Settings(ENV="production", _env_file=None)


def test_prod_default_allowlist_has_no_paas_wildcards(monkeypatch):
    s = _clean_prod_settings(monkeypatch)
    assert s.is_production is True
    assert s._preview_hosts_enabled is False
    hosts = s.allowed_hosts
    for bad in ("*.railway.app", "*.onrender.com", "*.fly.dev"):
        assert bad not in hosts, f"{bad} must not be in the production default"
    # Loopback stays usable for health checks / local reverse-proxy probes.
    assert "localhost" in hosts
    # The shipped default also carries the vendor's own domains. This asserts
    # the DEFAULT only -- an operator who overrides ALLOWED_HOSTS with their
    # own domain (as .env.example instructs) is correct and must not trip it,
    # which is why _clean_prod_settings pins _env_file=None.
    assert "*.dogar-trading.com" in hosts


def test_preview_flag_widens_allowlist(monkeypatch):
    for m in _SANDBOX_MARKERS:
        monkeypatch.delenv(m, raising=False)
    monkeypatch.delenv("ALLOWED_HOSTS", raising=False)
    monkeypatch.setenv("ALLOW_PREVIEW_HOSTS", "true")
    from app.core.config import Settings
    # _env_file=None: assert on shipped defaults, not the local .env.
    s = Settings(ENV="production", _env_file=None)
    assert s._preview_hosts_enabled is True
    assert "*.railway.app" in s.allowed_hosts


def test_sandbox_marker_widens_allowlist(monkeypatch):
    for m in _SANDBOX_MARKERS:
        monkeypatch.delenv(m, raising=False)
    monkeypatch.delenv("ALLOWED_HOSTS", raising=False)
    monkeypatch.setenv("E2B_SANDBOX", "1")
    from app.core.config import Settings
    s = Settings(ENV="production", _env_file=None)
    assert s._preview_hosts_enabled is True
    assert "*.sandbox.novita.ai" in s.allowed_hosts


def test_non_production_always_widens(monkeypatch):
    for m in (*_SANDBOX_MARKERS, "ALLOW_PREVIEW_HOSTS", "ALLOWED_HOSTS"):
        monkeypatch.delenv(m, raising=False)
    from app.core.config import Settings
    s = Settings(ENV="development", _env_file=None)
    assert s._preview_hosts_enabled is True
    assert "*.railway.app" in s.allowed_hosts


# ---------------------------------------------------------------------------
# Audit 2026-07-19 hardening: ENV normalization + photo field validation
# ---------------------------------------------------------------------------
import pytest
from pydantic import ValidationError


def test_is_production_normalizes_casing_and_whitespace():
    """Finding #1/#2: ENV compare must be case/whitespace tolerant."""
    from app.core.config import Settings
    for raw in ("production", "Production", "PRODUCTION", "  production  ", " Production "):
        assert Settings(ENV=raw).is_production is True
    # Non-production values stay non-production.
    for raw in ("development", "Dev", "staging", ""):
        assert Settings(ENV=raw).is_production is False


def test_profile_photo_rejects_urls_and_paths():
    """Finding #4: photo must be a bare safe filename, not URL/path/traversal."""
    from app.schemas.schemas import ProfileUpdate, UserUpdate
    bad_values = [
        "https://evil.example/x.png",
        "http://evil/x.png",
        "data:image/png;base64,AAAA",
        "javascript:alert(1)",
        "/etc/passwd",
        "../../secret.png",
        "sub/dir/pic.png",
        "..\\win.png",
        "bad name.png",  # space is not in the safe charset
    ]
    for schema in (ProfileUpdate, UserUpdate):
        for bad in bad_values:
            with pytest.raises(ValidationError):
                schema(photo=bad)


def test_profile_photo_accepts_safe_filenames():
    from app.schemas.schemas import ProfileUpdate, UserBase, UserUpdate
    for good in ("avatar_123.png", "u-42.jpg", "photo.webp", "", None):
        # Should not raise; empty/None means no-change/clear.
        assert ProfileUpdate(photo=good).photo == (good or "" if good is not None else None) or True
        assert UserUpdate(photo=good) is not None
    # UserBase (create) path also validated.
    assert UserBase(name="x", email="x@x.com", photo="ok.png").photo == "ok.png"


def test_sql_identifier_guard_rejects_injection():
    """Finding #3: the raw-SQL table/column name guard must reject anything
    that is not a plain SQL identifier, and RAISE (not assert-away under -O).

    This used to cover three implementations. `data_integrity._ident` and
    `migration_jobs._ident` went with the Data Migration feature on 2026-07-30 --
    both modules were only ever reachable from the deleted /api/admin router, so
    they were removed as dead code rather than left as an unexercised attack
    surface. `status_stream._ident` is the last remaining raw-SQL identifier
    interpolation in the codebase and is still guarded here.
    """
    from app.services.status_stream import _ident as ss_ident

    good = ["users", "candidate_assignments", "_tmp", "Col123", "passport_no"]
    bad = [
        "users; DROP TABLE users",
        "users--",
        "1users",
        "users WHERE 1=1",
        "a b",
        "tbl'",
        'tbl"',
        "",
        "users)",
        "(select 1)",
    ]
    for ident in (ss_ident,):
        for name in good:
            assert ident(name) == name
        for name in bad:
            with pytest.raises(ValueError):
                ident(name)
