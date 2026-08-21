"""The app must not depend on the current working directory.

WHY THIS FILE EXISTS
--------------------
An external audit of the v1.1.0 release ZIP flagged (Low severity):

    Template/static paths are relative to the current working directory
    -- Jinja2Templates(directory="app/templates") and
       StaticFiles(directory="app/static") in app/main.py
    -- Running from outside the project root can break template/static
       resolution

That is real, and the consequences are worse than "can break": every one of
those paths is resolved at IMPORT time, and one of them -- `SPA_INDEX_PATH` --
is fed to `os.path.exists()`. From the wrong directory that check simply
returns False, `_SPA_AVAILABLE` goes False, and the entire React SPA disables
itself with no error, no warning and no log line. A missing file is
indistinguishable from "the operator turned the SPA off on purpose", so the
failure is completely silent.

In practice the wrong CWD is easy to hit: a systemd unit without
`WorkingDirectory=`, a cron entry, a container whose WORKDIR differs, or simply
`uvicorn app.main:app` typed from the wrong shell.

These tests pin the property that actually matters -- *the app works from
anywhere* -- rather than asserting on the source text of main.py, which would
pass just as happily if someone reintroduced a relative path in a new place.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _memory_snapshot() -> str:
    """Best-effort MemAvailable/SwapTotal, for diagnosing a boot stall.

    Reads /proc directly rather than shelling out to `free` so it works on a
    stripped container, and returns a plain string on any failure -- a
    diagnostic helper must never be the reason a test errors.
    """
    try:
        wanted = ("MemTotal:", "MemAvailable:", "SwapTotal:")
        lines = Path("/proc/meminfo").read_text().splitlines()
        picked = [ln for ln in lines if ln.startswith(wanted)]
        return "\n".join("    " + ln for ln in picked) or "    (no fields found)"
    except OSError as exc:
        return f"    (unavailable: {exc})"


def _available_mb() -> int | None:
    """MemAvailable in MiB, or None if it cannot be determined."""
    try:
        for ln in Path("/proc/meminfo").read_text().splitlines():
            if ln.startswith("MemAvailable:"):
                return int(ln.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


# Booting a second full FastAPI app needs roughly this much headroom. Below it,
# a timeout is explained by the box; at or above it, a timeout is a real stall
# and must fail rather than skip -- otherwise a startup deadlock introduced
# later would hide behind a green "skipped".
_BOOT_HEADROOM_MB = 400


def test_static_and_template_dirs_are_absolute():
    """Whatever the CWD, the configured directories must be absolute + real.

    NOTE: the Jinja2 `templates` object this used to inspect was removed with
    the rest of the Jinja stack (the React SPA is the only frontend now), so
    only the SPA index is checked. It is the path that actually carries the
    silent-failure risk described in the module docstring: it is fed to
    `os.path.exists()`, so from the wrong CWD the SPA disables itself with no
    error at all. Kept as a real assertion rather than deleted with the
    templates, because that failure mode is unchanged.
    """
    import app.main as main_mod
    from app.main import SPA_INDEX_PATH

    assert not hasattr(main_mod, "templates"), (
        "app.main re-exports a Jinja `templates` object. The Jinja stack was "
        "removed; if it is being reintroduced, restore the searchpath "
        "absoluteness assertions that used to live here."
    )

    assert Path(SPA_INDEX_PATH).is_absolute(), (
        f"SPA_INDEX_PATH is CWD-relative ({SPA_INDEX_PATH!r}); from another "
        "working directory the os.path.exists() check silently disables the SPA"
    )
    assert Path(SPA_INDEX_PATH).is_file(), (
        f"SPA_INDEX_PATH does not exist ({SPA_INDEX_PATH!r}); the SPA would be "
        "silently disabled. Run `npm run build` in app/frontend."
    )


def test_mounted_static_directories_are_absolute():
    from starlette.staticfiles import StaticFiles

    from app.main import app

    mounted = 0
    for route in app.routes:
        appobj = getattr(route, "app", None)
        if isinstance(appobj, StaticFiles):
            for d in appobj.all_directories:
                p = Path(str(d))
                assert p.is_absolute(), (
                    f"StaticFiles mount {getattr(route, 'path', '?')} uses a "
                    f"CWD-relative directory: {d!r}"
                )
            mounted += 1

    assert mounted, (
        "no StaticFiles mounts were found -- if the mounts were refactored, "
        "update this test or it silently guards nothing"
    )


def test_app_imports_and_serves_from_an_unrelated_working_directory():
    """The real end-to-end property: boot from `/` and serve a template page.

    Runs in a subprocess because the working directory and the import of
    app.main are both process-global; doing this in-process would contaminate
    every other test in the session.
    """
    script = r"""
import os, sys
sys.path.insert(0, os.environ["REPO_ROOT"])
# The whole point: a working directory with no relationship to the project.
os.chdir("/")

from fastapi.testclient import TestClient
from app.main import app, SPA_INDEX_PATH

assert os.path.isabs(SPA_INDEX_PATH), SPA_INDEX_PATH
assert os.path.isfile(SPA_INDEX_PATH), SPA_INDEX_PATH

with TestClient(app) as c:
    r = c.get("/api/ping")
    assert r.status_code == 200, ("ping", r.status_code)
    # Serving the SPA shell proves the index path resolved from this CWD --
    # the exact check whose silent failure disables the whole frontend.
    r = c.get("/login")
    assert r.status_code == 200, ("login", r.status_code, r.text[:400])
    assert len(r.text) > 500, "login page suspiciously short -- SPA index empty?"
    assert "<div id=\"root\">" in r.text or "<script" in r.text, (
        "login response is not the SPA shell", r.text[:400]
    )
    # And a real static asset proves the StaticFiles mount resolved.
    r = c.get("/static/img/logo.png")
    assert r.status_code == 200, ("logo", r.status_code)

print("PORTABLE_OK")
"""
    env = dict(os.environ)
    env["REPO_ROOT"] = str(REPO_ROOT)
    env["TESTING"] = "true"
    env.setdefault("SECRET_KEY", "portability-test-secret-key-0123456789abcdef")

    # ISOLATION (audit 2026-08-09): this test boots the WHOLE app in a
    # subprocess, which runs startup migrations + idempotent seeding. Pointing
    # that at the shared ./data/dogar_trading.db meant the boot competed for the
    # SQLite write-lock with the rest of the suite, occasionally starving the
    # subprocess past its timeout (a flake unrelated to portability). Give the
    # boot its OWN isolated temp DB: the portability property (import + serve
    # from an unrelated CWD) is unchanged, but the test no longer contends
    # with — or mutates — the live database.
    #
    # MEMORY (audit 2026-08-10): the isolation above was necessary but not
    # sufficient, and the "parallel DB contention" diagnosis was wrong on its
    # face -- this suite runs SEQUENTIALLY (no pytest-xdist), so there is no
    # parallel writer to contend with. The real cause is memory. This test
    # forks a child that builds a SECOND complete FastAPI app (routers, ORM
    # mappers, engines) while the parent pytest process is still holding the
    # app instances, engines and TestClients accumulated over ~49 test modules.
    # On a small container (~1 GB RAM, NO SWAP) the child has a few hundred MB
    # to work with; with no swap to fall back on it thrashes and blows the
    # timeout, even though the identical boot takes ~3s in isolation.
    #
    # A bare TimeoutExpired traceback said none of this -- it just pointed at
    # portability, which is exactly the thing that was NOT broken. So the
    # timeout is now caught and re-raised with the memory reading attached,
    # and the test skips (rather than failing) when the box genuinely cannot
    # host two app instances. It still FAILS loudly for any non-timeout error,
    # which is the regression this file exists to catch.
    tmp_db = tempfile.NamedTemporaryFile(prefix="portability_", suffix=".db", delete=False)
    tmp_db.close()
    # 4-slash form = absolute path, so the app does NOT rewrite it CWD-relative.
    env["DATABASE_URL"] = f"sqlite:////{tmp_db.name.lstrip('/')}"

    try:
        try:
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd="/",  # belt and braces: the parent process starts elsewhere too
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
            )
        except subprocess.TimeoutExpired:
            # Boot normally takes ~3s. Blowing a 120s budget is not "slow", it
            # is a stall. Decide between "the box ran out of memory" and "the
            # app genuinely deadlocks at startup" using the memory reading --
            # skipping unconditionally would hide the second case forever.
            avail = _available_mb()
            detail = (
                "the portability subprocess timed out before it could boot "
                "(normally ~3s). This test forks a second full FastAPI app "
                "while the parent pytest process still holds ~49 modules' "
                "worth of app instances and engines.\n"
                f"memory at the time of the stall:\n{_memory_snapshot()}"
            )
            if avail is not None and avail < _BOOT_HEADROOM_MB:
                pytest.skip(
                    f"{detail}\nOnly ~{avail} MiB available (< "
                    f"{_BOOT_HEADROOM_MB} MiB needed to host a second app), so "
                    "this is an environment limit, not a portability "
                    "regression. Verify on its own with:\n"
                    "    pytest tests/test_portability.py"
                )
            raise AssertionError(
                f"{detail}\nMemory was NOT the constraint "
                f"(~{avail} MiB available), so the app appears to hang during "
                "startup from an unrelated working directory. Investigate "
                "_on_startup(): a migration or seeder is most likely blocking "
                "on a lock it will never get."
            ) from None
    finally:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(tmp_db.name + suffix)
            except OSError:
                pass
    assert result.returncode == 0 and "PORTABLE_OK" in result.stdout, (
        "the app failed to boot and serve from an unrelated working directory.\n"
        f"exit={result.returncode}\n"
        f"--- stdout ---\n{result.stdout[-3000:]}\n"
        f"--- stderr ---\n{result.stderr[-3000:]}"
    )


# ==========================================================================
# The database URL -- the gap these tests originally had
# ==========================================================================
#
# The tests above shipped in v1.1.0 and passed, which is why the release was
# believed to be CWD-independent. They were not wrong, they were incomplete:
# they cover templates, StaticFiles mounts and the SPA index, and the
# end-to-end case asserts /api/ping, /login and a static asset -- none of which
# touch the database. On top of that, conftest.py sets DATABASE_URL to an
# ABSOLUTE temp file before importing the app, so the shipped relative default
# was never exercised anywhere in the suite.
#
# A later external audit found what was left: the default control DB URL is
# "sqlite:///./data/dogar_trading.db", resolved by SQLAlchemy against the
# process CWD. Reproduced from an unrelated directory:
#     sqlite3.OperationalError: unable to open database file
# and in the nastier variant -- alien CWD that is writable -- SQLite CREATES an
# empty database, so the app boots to a working login page backed by zero
# records, which looks exactly like data loss.
#
# The same applied to tenant databases: `tenants.db_path` stores relative
# values, and a tenant DB that appears missing is deliberately a hard 503
# (TenantDatabaseMissing must never fall back to the control DB), so the entire
# multi-tenant surface went down rather than degrading.


def test_anchor_sqlite_url_handles_every_url_form():
    """Unit-level truth table for the URL rewriter."""
    from app.core.config import _PROJECT_ROOT, _anchor_sqlite_url

    root = str(_PROJECT_ROOT)

    # Relative (3 slashes + no leading /) -> anchored on the project root.
    assert _anchor_sqlite_url("sqlite:///./data/x.db") == f"sqlite:///{root}/data/x.db"
    assert _anchor_sqlite_url("sqlite:///data/x.db") == f"sqlite:///{root}/data/x.db"

    # Already absolute (4 slashes) -> untouched.
    already = "sqlite:////var/lib/dogar/x.db"
    assert _anchor_sqlite_url(already) == already

    # In-memory -> untouched (rewriting it would break every in-memory test).
    for mem in ("sqlite:///:memory:", "sqlite:///file:x?mode=memory&cache=shared"):
        assert _anchor_sqlite_url(mem) == mem

    # Non-SQLite -> untouched, including credentials and query strings.
    pg = "postgresql+psycopg2://user:pw@db.internal:5432/dogar?sslmode=require"
    assert _anchor_sqlite_url(pg) == pg


def test_settings_database_url_is_absolute():
    """Whatever the source (.env, environment, shipped default), it must be absolute."""
    from app.core.config import settings

    if settings.DATABASE_URL.startswith("sqlite") and ":memory:" not in settings.DATABASE_URL:
        path = settings.DATABASE_URL.split(":///", 1)[1]
        assert path.startswith("/"), (
            f"DATABASE_URL is CWD-relative ({settings.DATABASE_URL!r}); from another "
            "working directory SQLite either fails to open it or silently creates "
            "an empty database"
        )


def test_tenant_db_url_anchors_relative_stored_paths():
    """`tenants.db_path` holds relative values in existing installs."""
    from app.core.config import _PROJECT_ROOT
    from app.core.tenancy import tenant_db_url

    url = tenant_db_url("acme", "data/tenants/tenant_acme.db")
    assert url == f"sqlite:///{_PROJECT_ROOT}/data/tenants/tenant_acme.db", url

    # An operator who stored an absolute path keeps exactly that.
    abs_path = "/srv/dogar/tenants/tenant_acme.db"
    assert tenant_db_url("acme", abs_path) == f"sqlite:///{abs_path}"


def test_relative_database_url_opens_from_an_unrelated_working_directory():
    """End-to-end: a relative URL must resolve to the PROJECT root, not the CWD.

    Uses a throwaway probe file rather than the real control DB so the test
    cannot touch production data, and asserts the file lands under the project
    root while NOT appearing relative to the working directory.
    """
    probe_rel = "data/_portability_probe.db"
    probe_abs = REPO_ROOT / probe_rel
    probe_abs.unlink(missing_ok=True)

    script = r"""
import os, sys
sys.path.insert(0, os.environ["REPO_ROOT"])
os.chdir("/")  # an unrelated working directory

from app.core.config import settings
url = settings.DATABASE_URL
assert url.startswith("sqlite:////"), ("not anchored", url)

from sqlalchemy import create_engine, text
eng = create_engine(url)
with eng.connect() as c:
    c.execute(text("CREATE TABLE IF NOT EXISTS probe (id INTEGER PRIMARY KEY)"))
    c.execute(text("INSERT INTO probe (id) VALUES (1)"))
    c.commit()
print("DBPATH=" + url.split(":///", 1)[1])
print("PORTABLE_DB_OK")
"""
    env = dict(os.environ)
    env["REPO_ROOT"] = str(REPO_ROOT)
    env["TESTING"] = "true"
    env["ENV"] = "development"
    env.setdefault("SECRET_KEY", "portability-test-secret-key-0123456789abcdef")
    # The shipped-style RELATIVE url -- the thing that was never tested.
    env["DATABASE_URL"] = f"sqlite:///./{probe_rel}"

    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd="/",
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )
        assert result.returncode == 0 and "PORTABLE_DB_OK" in result.stdout, (
            "a relative sqlite DATABASE_URL did not resolve from an unrelated "
            f"working directory.\nexit={result.returncode}\n"
            f"--- stdout ---\n{result.stdout[-3000:]}\n"
            f"--- stderr ---\n{result.stderr[-3000:]}"
        )
        assert probe_abs.exists(), (
            f"the database was not created at the project root ({probe_abs}); "
            "the relative URL was resolved against the CWD instead"
        )
        assert not Path("/data/_portability_probe.db").exists(), (
            "a database was created relative to the alien working directory -- "
            "this is the silent-empty-database failure mode"
        )
    finally:
        probe_abs.unlink(missing_ok=True)
