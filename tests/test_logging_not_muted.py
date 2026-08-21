"""Application logging must survive startup.

WHY THIS FILE EXISTS
--------------------
Found on 2026-08-01 while fixing the realtime status-stream table mapping: the
warning that fix added never appeared. The reason had nothing to do with the
warning.

``app/main.py::_on_startup()`` calls ``run_schema_migrations()``, which runs
``alembic upgrade head`` **in-process**. Alembic's ``env.py`` called
``logging.config.fileConfig(config.config_file_name)``, and that function's
``disable_existing_loggers`` parameter defaults to ``True``. ``alembic.ini``
declares only ``root``, ``sqlalchemy`` and ``alembic``, so every other logger
that had already been created was set to ``disabled = True`` for the rest of the
process -- on every single boot.

Measured before the fix, 14 of 18 ``dtc.*`` loggers were dead after startup:

    dtc.auth        dtc.cache      dtc.config     dtc.documents
    dtc.exception   dtc.letterhead_renderer       dtc.ocr
    dtc.pdf_engine  dtc.rbac       dtc.security   dtc.sentry
    dtc.settings    dtc.startup    dtc.status_stream    dtc.tenancy

So the app could not report: authentication and RBAC decisions, security events,
unhandled exception detail, tenant resolution failures, or the overlay engine's
"this field is off the page and will not appear" warning. Loggers created *after*
startup (``dtc.request``) still worked, which is exactly why request logs looked
healthy and the hole went unnoticed through a release.

This is the mechanism behind "the same bug came back and nothing told me": the
application was muted. The tests below assert the property (logs still work
after a full startup cycle), not the fix (an argument in env.py), so any future
change that re-mutes logging -- by any route -- fails here.
"""
from __future__ import annotations

import logging

from fastapi.testclient import TestClient

# NOTE on import order: these submodule imports must come BEFORE `from app.main
# import app`, because `import app.services.pdf_engine` binds the name `app` to
# the package -- doing it afterwards silently shadows the ASGI app and TestClient
# fails with "'module' object is not callable".
# They exist so these module-level loggers are created *before* startup runs,
# which is exactly the ordering that made them vulnerable to being disabled.
import app.services.pdf_engine  # noqa: F401,E402
import app.services.status_stream  # noqa: F401,E402
from app.main import app as fastapi_app  # noqa: E402

# Loggers that carry information an operator cannot afford to lose.
CRITICAL_LOGGERS = [
    "dtc.security",
    "dtc.auth",
    "dtc.rbac",
    "dtc.exception",
    "dtc.tenancy",
    "dtc.pdf_engine",
    "dtc.status_stream",
    "dtc.config",
]


def test_no_dtc_logger_is_disabled_after_startup():
    """A full startup cycle must not mute any application logger."""
    with TestClient(fastapi_app) as client:  # __enter__ runs the startup handlers
        client.get("/api/health")

    disabled = [
        name
        for name, obj in logging.Logger.manager.loggerDict.items()
        if isinstance(obj, logging.Logger)
        and name.startswith("dtc")
        and obj.disabled
    ]
    assert not disabled, (
        "these application loggers were muted by startup: "
        f"{sorted(disabled)}. Something called logging.config.fileConfig() (or "
        "dictConfig) with disable_existing_loggers left at its True default -- "
        "in-process `alembic upgrade head` is the known culprit. The app cannot "
        "report security events, exceptions or document-geometry problems in "
        "this state."
    )


def test_critical_loggers_actually_emit_after_startup():
    """Not-disabled is necessary but not sufficient -- prove a record gets out."""
    with TestClient(fastapi_app) as client:
        client.get("/api/health")

    failures = []
    for name in CRITICAL_LOGGERS:
        logger = logging.getLogger(name)
        received: list[str] = []

        class _Collect(logging.Handler):
            # `sink` is bound as a default argument on purpose. Closing over the
            # loop's `received` list would make every handler created in this
            # loop share whichever list the variable happens to point at when
            # emit() finally runs (ruff B023) -- the classic late-binding trap,
            # and here it would let a genuinely muted logger pass by picking up
            # another logger's records.
            def emit(self, record, sink=received):  # noqa: D102
                sink.append(record.getMessage())

        handler = _Collect(level=logging.WARNING)
        logger.addHandler(handler)
        prev = logger.level
        logger.setLevel(logging.WARNING)
        try:
            logger.warning("logging self-check for %s", name)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(prev)
        if not received:
            failures.append(name)

    assert not failures, (
        f"these loggers accepted no records after startup: {failures}. Warnings "
        "written by the app -- including the overlay engine's off-page field "
        "warning -- would be silently dropped in production."
    )


def test_alembic_env_does_not_disable_existing_loggers():
    """Guard the specific known cause, with a message that explains itself.

    The behavioural tests above are the real contract; this one exists so the
    failure output names the file and argument to change instead of leaving the
    next person to rediscover it.
    """
    from pathlib import Path

    env = Path(__file__).resolve().parents[1] / "alembic" / "env.py"
    src = env.read_text(encoding="utf-8")
    assert "fileConfig(" in src, "alembic/env.py no longer configures logging?"
    assert "disable_existing_loggers=False" in src, (
        "alembic/env.py calls fileConfig() without disable_existing_loggers=False. "
        "Because migrations run in-process at startup, this mutes every logger "
        "created before startup for the life of the process."
    )


# ===========================================================================
# SECOND MUTING MECHANISM -- the LEVEL, measured 2026-08-19
# ===========================================================================
# The 2026-08-01 fix above stopped alembic from *disabling* existing loggers.
# It did not stop it from lowering the bar underneath them.
#
# `logging.config.fileConfig()` does not merely toggle `disabled`: it REBUILDS
# the root logger from the ini file. alembic.ini declares
#
#     [logger_root]
#     level = WARN
#
# so every in-process `command.upgrade()` / `command.stamp()` call reset the
# application's root logger from INFO to WARNING, and replaced our
# JSONFormatter StreamHandler with alembic's plain console handler -- for the
# whole process, on every boot.
#
# Measured with a probe either side of the real call:
#
#     root level BEFORE alembic: INFO      ('probe-before' emitted as JSON)
#     root level AFTER  alembic: WARNING   ('probe-after'  never emitted)
#
# Every INFO the app writes after startup was therefore lost in production:
# the startup diagnostics, "schema migrations applied", the tenant
# schema-drift check, and any INFO-level audit context.
#
# This hid behind the earlier fix precisely because the loggers were no longer
# *disabled* -- they were simply below the new threshold, so
# test_no_dtc_logger_is_disabled_after_startup still passed. And
# test_critical_loggers_actually_emit_after_startup could not see it either,
# because it probes at WARNING and sets the level itself. The tests below
# assert at INFO and never touch the level, which is what makes them capable
# of failing.

def test_root_logger_still_accepts_info_after_a_migration_run():
    """The exact regression: INFO must survive an in-process alembic call."""
    from app.core.logging import configure_logging
    from app.db.init_db import run_schema_migrations

    configure_logging()
    before = logging.getLogger().level
    assert before <= logging.INFO, (
        "test precondition failed: configure_logging() did not leave the root "
        f"logger at INFO or lower (got {logging.getLevelName(before)})"
    )

    run_schema_migrations()

    after = logging.getLogger().level
    assert after == before, (
        "an in-process alembic migration changed the ROOT logger level from "
        f"{logging.getLevelName(before)} to {logging.getLevelName(after)}. "
        "Every INFO the application writes for the rest of this process is "
        "silently dropped -- including the startup diagnostics and the tenant "
        "schema-drift warning. Save/restore the root level around "
        "command.upgrade() (see app/db/init_db.py::_restore_root_logging)."
    )


def test_an_info_record_actually_gets_out_after_a_migration_run():
    """Level parity is necessary but not sufficient -- prove a record emits.

    Deliberately does NOT call setLevel() on the probe logger: forcing the
    level is what made the pre-existing WARNING-based test blind to this bug.
    """
    from app.core.logging import configure_logging
    from app.db.init_db import run_schema_migrations

    configure_logging()
    run_schema_migrations()

    received: list[str] = []

    class _Collect(logging.Handler):
        def emit(self, record, sink=received):  # noqa: D102
            sink.append(record.getMessage())

    root = logging.getLogger()
    handler = _Collect()
    root.addHandler(handler)
    try:
        logging.getLogger("dtc.migrate").info("post-migration info self-check")
    finally:
        root.removeHandler(handler)

    assert received, (
        "an INFO record written after an in-process migration never reached a "
        "root handler. The application cannot report on itself in production."
    )


def test_the_json_formatter_survives_a_migration_run():
    """Log FORMAT must survive too, not just the level.

    alembic.ini installs a plain `generic` console handler. If it replaces
    ours, the log pipeline that parses JSON gets unparseable lines -- which
    looks like "logging stopped working" from the outside even though records
    are being written.
    """
    from app.core.logging import JSONFormatter, configure_logging
    from app.db.init_db import run_schema_migrations

    configure_logging()
    run_schema_migrations()

    formatters = [type(h.formatter) for h in logging.getLogger().handlers]
    assert JSONFormatter in formatters, (
        "after an in-process migration the root logger has no JSONFormatter "
        f"handler left (formatters: {formatters}). alembic.ini's plain console "
        "handler replaced it, so structured logging is broken for the rest of "
        "the process."
    )


def test_tenant_provisioning_does_not_mute_the_app():
    """`command.stamp` mutes logging the same way `command.upgrade` does.

    This one matters because _stamp_alembic_head_on() runs inside the LIVE web
    app: an admin provisioning a new office would mute the portal's own logs
    as a side effect of a routine action.
    """
    import sqlalchemy as sa

    from app.core.logging import configure_logging
    from app.services.tenant_service import _stamp_alembic_head_on

    configure_logging()
    before_level = logging.getLogger().level

    engine = sa.create_engine("sqlite://")  # throwaway in-memory DB
    try:
        _stamp_alembic_head_on(engine)
    finally:
        engine.dispose()

    after_level = logging.getLogger().level
    assert after_level == before_level, (
        "provisioning a tenant changed the root logger level from "
        f"{logging.getLevelName(before_level)} to "
        f"{logging.getLevelName(after_level)} -- creating an office silently "
        "mutes the portal."
    )
