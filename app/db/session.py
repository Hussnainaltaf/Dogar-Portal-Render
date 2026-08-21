"""Database session management -- SQLite (dev) or PostgreSQL (production).

The engine is built from ``settings.DATABASE_URL`` at import time. Both
``sqlite:///…`` and ``postgresql://…`` (or ``postgresql+psycopg2://…``)
schemes are supported. When running against Postgres the driver
(``psycopg2-binary``) must be installed; see requirements.txt.

Per-tenant engines (one Postgres database or one SQLite file per tenant)
are created lazily in ``app.core.tenancy._make_tenant_sessionmaker``; the
engine below is the CONTROL DB engine only.
"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings


def assert_supported_database_url(url: str) -> str:
    """Validate a DATABASE_URL scheme, returning it unchanged when supported.

    Extracted as a pure function (audit 2026-08-15) so the guard can be
    exercised directly. Re-importing this module to test the failure path is
    NOT a viable alternative: ``declarative_base()`` below would build a fresh
    ``Base``, orphaning all 31 mapped models from the metadata every other
    test relies on.
    """
    if url.startswith("sqlite") or url.startswith("postgres"):
        return url
    scheme = url.split("://", 1)[0] or "<empty>"
    raise RuntimeError(
        f"Unsupported DATABASE_URL scheme {scheme!r}. This portal supports "
        f"SQLite and PostgreSQL only:\n"
        f"  sqlite:///./data/dogar_trading.db      (default / single-office)\n"
        f"  postgresql://user:pass@host/dbname     (multi-tenant production)\n"
        f"Installing a driver for {scheme!r} will NOT make this work -- the "
        f"application has no {scheme!r} code path. Fix DATABASE_URL in .env."
    )


is_sqlite = settings.DATABASE_URL.startswith("sqlite")
is_postgres = settings.DATABASE_URL.startswith("postgres")

if is_sqlite:
    # SQLite: small pool, single-thread connection.
    engine = create_engine(
        settings.DATABASE_URL,
        connect_args={"check_same_thread": False, "timeout": 30},
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )
elif is_postgres:
    # PostgreSQL: real pool, pre_ping to survive server-side idle timeouts,
    # short connect_timeout so a network hiccup fails fast instead of stalling
    # a request thread. pool_recycle avoids the "server closed the connection
    # unexpectedly" bug after long idles behind PgBouncer / load balancers.
    engine = create_engine(
        settings.DATABASE_URL,
        connect_args={"connect_timeout": 10},
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        pool_recycle=1800,
    )
else:
    # AUDIT 2026-08-15 (Medium): the comment above claimed SQLAlchemy would
    # "raise a clear error at first connect". Both halves were wrong.
    #
    # 1. It does not wait for first connect. create_engine() imports the DBAPI
    #    driver EAGERLY, so an unsupported scheme explodes at *module import*
    #    -- i.e. during app startup, before any route exists.
    # 2. The error is not clear. DATABASE_URL=mysql://... produces:
    #        ModuleNotFoundError: No module named 'MySQLdb'
    #    raised from deep inside sqlalchemy/dialects/mysql/mysqldb.py, with
    #    nothing naming DATABASE_URL, the portal, or the fact that MySQL is
    #    not a supported backend at all. Operators reasonably read that as a
    #    missing OS package and go install python3-mysqldb / mysqlclient --
    #    which cannot help, because this application only supports SQLite and
    #    PostgreSQL (there is no MySQL code path anywhere in the codebase).
    #
    # Fail fast with a message that names the real problem and the fix.
    assert_supported_database_url(settings.DATABASE_URL)
    raise AssertionError("unreachable: assert_supported_database_url must raise")

# ---- SQLite performance pragmas (huge win for read-heavy workloads) ----
if is_sqlite:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _conn_record):
        cur = dbapi_conn.cursor()
        # Write-Ahead Logging → concurrent readers + writer don't block
        cur.execute("PRAGMA journal_mode=WAL;")
        # OS-only sync (still ACID under WAL) -- much faster commits
        cur.execute("PRAGMA synchronous=NORMAL;")
        # 64 MB page cache (negative = KB), big improvement on the 2.78MB DB
        cur.execute("PRAGMA cache_size=-65536;")
        # Memory-mapped I/O up to 256 MB
        cur.execute("PRAGMA mmap_size=268435456;")
        # Keep temp tables in RAM
        cur.execute("PRAGMA temp_store=MEMORY;")
        # 30s busy-wait before "database is locked"
        cur.execute("PRAGMA busy_timeout=30000;")
        # Foreign keys ON (data integrity)
        cur.execute("PRAGMA foreign_keys=ON;")
        cur.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
