"""Tests for the multi-tenant subdomain resolver and per-tenant
letterhead/asset endpoints."""
from __future__ import annotations

import io

from fastapi.testclient import TestClient

from app.core.tenancy import ROOT_HOSTS, extract_subdomain


# --------------------------- Subdomain parsing ----------------------
def test_extract_subdomain_localhost_is_none():
    """localhost / 127.0.0.1 / bare IPs must NOT be treated as tenants."""
    assert extract_subdomain("localhost") is None
    assert extract_subdomain("localhost:3000") is None
    assert extract_subdomain("127.0.0.1") is None
    assert extract_subdomain("0.0.0.0:3000") is None


def test_extract_subdomain_two_label_root_is_none():
    """A bare 2-label apex (example.com) must not be parsed as a tenant."""
    assert extract_subdomain("dogar.com") is None
    # 3+ labels DO get parsed -- that's the intended subdomain pattern
    # used by demo.oep.com.pk → 'demo' (test below).


def test_extract_subdomain_www_app_filtered():
    """www / app / portal are common roots, not tenants."""
    for prefix in ("www", "app"):
        assert extract_subdomain(f"{prefix}.dogar.com") is None


def test_extract_subdomain_picks_first_label_when_3plus():
    """demo.oep.com.pk → 'demo'"""
    val = extract_subdomain("demo.oep.com.pk")
    assert val == "demo"


def test_extract_subdomain_strips_port():
    assert extract_subdomain("demo.oep.com.pk:8080") == "demo"


def test_root_hosts_includes_localhost():
    assert "localhost" in ROOT_HOSTS
    assert "127.0.0.1" in ROOT_HOSTS


# --------------------------- /api/tenants/_meta/current -------------
def test_meta_current_no_subdomain_is_default(client: TestClient):
    """When the Host header is localhost the middleware should
    return the default-tenant metadata."""
    r = client.get("/api/tenants/_meta/current",
                   headers={"Host": "localhost:3000"})
    assert r.status_code == 200
    body = r.json()
    assert body["is_default"] is True
    assert body["slug"] is None
    assert body["company_name"]
    assert body["letterhead_url"] is None or body["letterhead_url"] == ""


# --------------------------- Branding endpoints (smoke) -------------
def test_branding_endpoints_require_auth(client: TestClient):
    """Unauthenticated requests to branding endpoints must 401."""
    # No cookie / no Authorization header → 401
    r = client.put("/api/tenants/999/branding", json={"office_name": "X"})
    assert r.status_code in (401, 403)


def test_branding_404_for_missing_tenant(client: TestClient, auth_headers: dict):
    """Admin-auth'd PUT to a non-existent tenant returns 404."""
    r = client.put("/api/tenants/99999/branding",
                   json={"office_name": "Ghost Office"},
                   headers=auth_headers)
    assert r.status_code == 404


def test_logo_upload_404_for_missing_tenant(client: TestClient, auth_headers: dict):
    """POST /tenants/{id}/logo on a missing tenant must 404."""
    files = {"file": ("logo.png", io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16), "image/png")}
    r = client.post("/api/tenants/99999/logo",
                    files=files, headers=auth_headers)
    assert r.status_code == 404


# --------------------------- Fail-closed + unbound-slug fix ----------
# Audit finding (2026-07-07): resolve_tenant_from_host() had (a) an unbound
# `slug` NameError when a tenant matched via custom_domain, and (b) a
# fail-OPEN path that returned None (→ control DB) when a resolved tenant's
# SQLite file was missing. These tests pin the corrected behaviour.
import os as _os
import tempfile as _tempfile
from unittest import mock as _mock

from app.core import tenancy as _tenancy
from app.core.tenancy import (
    TenantDatabaseMissing,
    resolve_tenant_from_host,
)


class _FakeTenant:
    """Minimal stand-in for a control-DB Tenant row."""
    def __init__(self, slug, db_path, custom_domain=None):
        self.id = 1
        self.slug = slug
        self.db_path = db_path
        self.custom_domain = custom_domain
        self.status = "active"
        self.company_name = "Acme Travels"
        self.short_name = "Acme"
        self.primary_color = "#123456"
        self.logo_filename = None


class _FakeQuery:
    def __init__(self, row):
        self._row = row
    def filter(self, *a, **k):
        return self
    def first(self):
        return self._row


class _FakeSession:
    def __init__(self, row):
        self._row = row
    def query(self, *a, **k):
        return _FakeQuery(self._row)
    def close(self):
        pass


def _patch_session(monkeypatch, row):
    monkeypatch.setattr(_tenancy, "ControlSessionLocal", lambda: _FakeSession(row))


def test_resolve_custom_domain_does_not_raise_unbound_slug(monkeypatch, tmp_path):
    """A tenant matched via custom_domain (never touches the subdomain branch)
    must resolve WITHOUT a NameError on `slug`, and expose the right slug."""
    db_file = tmp_path / "tenant_acme.db"
    db_file.write_bytes(b"")  # file exists
    row = _FakeTenant(slug="acme", db_path=str(db_file),
                      custom_domain="portal.acme.pk")
    _patch_session(monkeypatch, row)
    resolved = resolve_tenant_from_host("portal.acme.pk")
    assert resolved is not None
    assert resolved.slug == "acme"
    assert resolved.db_path == str(db_file)


def test_resolve_missing_db_hard_fails_not_fallback(monkeypatch):
    """When a tenant resolves but its DB file is MISSING, we must raise
    TenantDatabaseMissing — NOT return None (which would fall back to the
    control DB and leak root data under a tenant host)."""
    row = _FakeTenant(slug="ghost", db_path="/nonexistent/tenant_ghost.db",
                      custom_domain="portal.ghost.pk")
    _patch_session(monkeypatch, row)
    try:
        resolve_tenant_from_host("portal.ghost.pk")
        assert False, "expected TenantDatabaseMissing to be raised"
    except TenantDatabaseMissing as exc:
        assert exc.slug == "ghost"
        assert "tenant_ghost.db" in exc.db_path


# =====================================================================
# Section 3 -- storage-boundary regressions (audit 2026-08-15)
#
# These guard three defects found while investigating a report that a
# "missing MySQLdb module" was breaking backend startup. The MySQL claim
# was false (this codebase has no MySQL code path at all), but chasing it
# surfaced three genuine bugs in the tenant storage boundary.
#
# All three assert on OBSERVABLE BEHAVIOUR -- the URL the engine is
# actually bound to, the file that actually appears on disk, the message
# actually raised -- never on source text. Reverting any fix makes the
# corresponding test fail.
# =====================================================================
import pytest as _pytest


def _clear_engine_cache():
    """Drop cached tenant sessionmakers so each test builds a fresh engine."""
    with _tenancy._ENGINES_LOCK:
        _tenancy._ENGINES.clear()


def test_tenant_engine_url_is_anchored_to_the_project_root(monkeypatch, tmp_path):
    """The tenant ENGINE must open the same absolute path tenant_db_url() says.

    REGRESSION (High): _make_tenant_sessionmaker built its SQLite URL inline
    as f"sqlite:///{db_path}", bypassing tenant_db_url() -- the function that
    documents itself as the ONLY place URL construction happens and that
    carries the project-root anchoring.

    Because tenants.db_path stores CWD-relative values, running the app from
    any directory other than the project root made the engine open a
    *relative* path. SQLite CREATES a missing file rather than erroring, so
    every tenant silently got a brand-new EMPTY database and the operator saw
    a working login screen backed by no data.
    """
    _clear_engine_cache()
    monkeypatch.setattr(_tenancy.settings, "TENANT_DB_URL_TEMPLATE", "", raising=False)
    rel = "data/tenants/tenant_anchortest.db"

    # Simulate systemd/Docker launching the process from a different CWD.
    monkeypatch.chdir(tmp_path)

    expected = _tenancy.tenant_db_url("anchortest", rel)
    sm = _tenancy._make_tenant_sessionmaker(rel, "anchortest")
    actual = str(sm.kw["bind"].url)
    _clear_engine_cache()

    assert actual == expected, (
        "the tenant engine is bound to a DIFFERENT path than tenant_db_url() "
        f"resolves.\n  tenant_db_url(): {expected}\n  engine bound to: {actual}\n"
        "A relative engine URL means SQLite silently creates an empty DB in "
        "the process CWD instead of opening the real tenant database."
    )
    assert actual.startswith("sqlite:////"), (
        f"tenant engine URL {actual!r} is not absolute (4 slashes). A "
        "CWD-relative tenant URL is the whole bug this test guards."
    )


def test_tenant_engine_does_not_create_a_stray_db_in_the_cwd(monkeypatch, tmp_path):
    """Connecting a tenant engine from a foreign CWD must not birth an empty DB.

    This is the *consequence* half of the bug above, asserted directly: it is
    the behaviour the operator actually experiences (a portal that logs in
    fine but shows zero records).

    Housekeeping: because the fix makes the engine resolve against the PROJECT
    ROOT, the connect below legitimately materialises a file under the real
    data/tenants/ directory. The first version of this test left that artifact
    behind, littering the operator's data directory with a bogus tenant DB on
    every run. We remove it in a finally block.
    """
    from app.core.config import anchor_project_path

    _clear_engine_cache()
    monkeypatch.setattr(_tenancy.settings, "TENANT_DB_URL_TEMPLATE", "", raising=False)
    rel = "data/tenants/tenant_straytest.db"
    anchored = _os.path.abspath(anchor_project_path(rel))
    pre_existing = _os.path.exists(anchored)

    monkeypatch.chdir(tmp_path)
    try:
        sm = _tenancy._make_tenant_sessionmaker(rel, "straytest")
        session = sm()
        try:
            # Force a real connect so SQLite would materialise the file.
            from sqlalchemy import text as _text
            session.execute(_text("SELECT 1"))
        finally:
            session.close()
            _clear_engine_cache()

        stray = tmp_path / rel
        assert not stray.exists(), (
            f"connecting the tenant engine created a stray empty database at "
            f"{stray} (inside the process CWD). The engine must resolve db_path "
            "against the project root, not the CWD."
        )
    finally:
        # Never leave a synthetic tenant DB in the real data directory.
        if not pre_existing:
            for suffix in ("", "-wal", "-shm"):
                if _os.path.exists(anchored + suffix):
                    _os.remove(anchored + suffix)


def test_missing_tenant_db_check_resolves_the_same_path_as_the_engine(monkeypatch, tmp_path):
    """The 'is the tenant DB missing?' guard must test the file the engine opens.

    REGRESSION (High): the guard called os.path.exists() on the RAW db_path
    (CWD-relative) while the engine anchored to the project root. From a
    foreign CWD the two disagreed, so an existing tenant DB was reported
    missing and every request to that tenant became a hard 503.
    """
    _clear_engine_cache()
    monkeypatch.setattr(_tenancy.settings, "TENANT_DB_URL_TEMPLATE", "", raising=False)

    # A tenant DB that really exists, addressed by a project-root-relative path.
    from app.core.config import anchor_project_path
    rel = "data/tenants/tenant_existstest.db"
    real = _os.path.abspath(anchor_project_path(rel))
    _os.makedirs(_os.path.dirname(real), exist_ok=True)
    created = not _os.path.exists(real)
    if created:
        open(real, "wb").close()

    try:
        monkeypatch.chdir(tmp_path)          # foreign CWD -- the trigger
        row = _FakeTenant(slug="existstest", db_path=rel,
                          custom_domain="portal.existstest.pk")
        _patch_session(monkeypatch, row)
        resolved = resolve_tenant_from_host("portal.existstest.pk")
        assert resolved is not None and resolved.slug == "existstest", (
            "a tenant whose database EXISTS was reported missing because the "
            "existence check resolved db_path against the process CWD while "
            "the engine resolves it against the project root."
        )
    finally:
        if created and _os.path.exists(real):
            _os.remove(real)
        _clear_engine_cache()


def test_unsupported_database_url_scheme_names_the_real_problem():
    """An unsupported DATABASE_URL must fail with an actionable message.

    REGRESSION (Medium): the fallback branch claimed SQLAlchemy would "raise a
    clear error at first connect". Both halves were false -- create_engine()
    imports the DBAPI eagerly (so it dies at *import*, during startup), and
    the error was a bare `ModuleNotFoundError: No module named 'MySQLdb'`
    from deep inside SQLAlchemy's MySQL dialect.

    Nothing in that traceback named DATABASE_URL or said MySQL is unsupported,
    so operators reasonably diagnosed it as a missing OS package and tried to
    install python3-mysqldb / mysqlclient -- which cannot possibly help,
    because this application has no MySQL code path.

    NOTE: this asserts against the extracted pure validator rather than
    re-importing app.db.session. Re-importing that module rebuilds
    ``declarative_base()``, orphaning all 31 mapped models from the metadata
    other tests depend on -- a real cross-test corruption this suite caught.
    """
    from app.db.session import assert_supported_database_url

    with _pytest.raises(RuntimeError) as excinfo:
        assert_supported_database_url("mysql://user:pw@localhost/portal")
    msg = str(excinfo.value)

    assert "mysql" in msg.lower(), f"error does not name the offending scheme: {msg}"
    assert "DATABASE_URL" in msg, f"error does not name the setting to fix: {msg}"
    for supported in ("sqlite", "postgresql"):
        assert supported in msg.lower(), (
            f"error does not tell the operator which backends ARE supported "
            f"(missing {supported!r}): {msg}"
        )
    assert "will NOT make this work" in msg, (
        "the error must explicitly steer the operator away from installing a "
        "driver, which is the exact wrong turn the old message invited: " + msg
    )


def test_the_startup_path_actually_uses_the_scheme_validator():
    """The validator must be WIRED INTO startup, not merely exist.

    A pure function nobody calls protects nothing. An earlier version of this
    test only asserted the function existed and that the live process was on a
    supported scheme -- and a mutation that deleted the call site entirely
    (restoring the silent ``create_engine`` fallback) sailed straight through
    it GREEN. That escape is the reason this test now executes the real
    module-level source against an unsupported URL.

    We compile and exec app/db/session.py in an isolated namespace with a
    stubbed ``settings``. That runs the genuine startup branch logic without
    re-importing the module into ``sys.modules`` -- which would rebuild
    ``declarative_base()`` and orphan all 31 mapped models from the metadata
    the rest of the suite depends on.
    """
    import pathlib
    import types

    src = pathlib.Path(_tenancy.__file__).parent.parent / "db" / "session.py"
    code = compile(src.read_text(encoding="utf-8"), str(src), "exec")

    fake_settings = types.SimpleNamespace(DATABASE_URL="mysql://u:pw@localhost/portal")
    fake_config = types.ModuleType("app.core.config")
    fake_config.settings = fake_settings

    import sys
    real_config = sys.modules.get("app.core.config")
    sys.modules["app.core.config"] = fake_config
    try:
        with _pytest.raises(RuntimeError) as excinfo:
            exec(code, {"__name__": "app.db.session_probe"})
    finally:
        if real_config is not None:
            sys.modules["app.core.config"] = real_config
        else:                                   # pragma: no cover
            sys.modules.pop("app.core.config", None)

    msg = str(excinfo.value)
    assert "mysql" in msg.lower() and "DATABASE_URL" in msg, (
        "app/db/session.py did not reach the scheme guard on an unsupported "
        f"URL -- startup would fall through to a raw create_engine(). Got: {msg}"
    )


def test_supported_schemes_are_accepted_by_the_validator():
    """The fail-fast guard must not reject the two SUPPORTED schemes.

    A guard that rejects everything would pass the test above while breaking
    production, so this pins the positive case.
    """
    from app.db.session import assert_supported_database_url

    for url in ("sqlite:///./data/dogar_trading.db",
                "sqlite:////abs/path.db",
                "postgresql://u:pw@localhost:5432/portal",
                "postgresql+psycopg2://u:pw@pg:5432/portal"):
        assert assert_supported_database_url(url) == url, (
            f"supported URL {url!r} was rejected by the scheme guard"
        )


def test_no_business_endpoint_is_bound_to_the_control_database():
    """Every business route must take its Session from get_tenant_db.

    This is THE structural invariant behind "each recruitment office is its
    own isolated tenant with a strictly separate data boundary". A route that
    declares ``Depends(get_db)`` talks to the CONTROL database no matter which
    tenant host the request arrived on -- so a tenant user hitting it would
    read and write Dogar's records.

    The only legitimate control-DB consumer is app/api/endpoints/tenants.py:
    the tenant registry itself lives in the control DB by definition (that is
    where the list of tenants and their db_path values is stored).

    This guard is deliberately structural rather than behavioural: a leak of
    this kind is invisible in single-tenant testing (control DB == the only
    DB), so it cannot be caught by exercising the default host. It fails the
    moment someone adds ``Depends(get_db)`` to a business route.
    """
    import re

    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(_tenancy.__file__)))
    endpoints = _os.path.join(root, "api", "endpoints")
    CONTROL_DB_ALLOWED = {"tenants.py"}

    offenders = []
    for fname in sorted(_os.listdir(endpoints)):
        if not fname.endswith(".py") or fname in CONTROL_DB_ALLOWED:
            continue
        lines = open(_os.path.join(endpoints, fname), encoding="utf-8").read().splitlines()
        starts = [i for i, ln in enumerate(lines)
                  if re.match(r'@router\.(get|post|put|patch|delete)\(', ln.strip())]
        for k, i in enumerate(starts):
            end = starts[k + 1] if k + 1 < len(starts) else len(lines)
            body = "\n".join(lines[i:end])
            if "Depends(get_db)" in body:
                m = re.match(r'@router\.(get|post|put|patch|delete)\(\s*"?([^",)]*)',
                             lines[i].strip())
                route = f"{m.group(1).upper()} {m.group(2) or '/'}" if m else "?"
                offenders.append(f"{fname}: {route}")

    assert not offenders, (
        "these routes are bound to the CONTROL database instead of the "
        "per-tenant database, so a tenant request would read/write the Dogar "
        "records:\n  " + "\n  ".join(offenders) +
        "\n\nUse `db: Session = Depends(get_tenant_db)`. On the root host it "
        "transparently yields the control DB, so single-tenant behaviour is "
        "unchanged."
    )
