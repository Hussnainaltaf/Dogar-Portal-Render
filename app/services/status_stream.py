"""App-layer "realtime" status change detection + health.

This app targets SQLite/Postgres via SQLAlchemy — there is NO Supabase /
Postgres logical-replication realtime channel. To keep candidate, demand-file
and document statuses fresh in the UI "even if realtime drops", we implement a
lightweight change-detection layer:

  * ``status_version(db)`` returns a cheap monotonic token derived from the row
    count plus a per-table change marker across the status-bearing tables
    (candidates, candidate_assignments, demands, generated_documents). The
    marker is the table's timestamp column where one exists; ``candidate_
    assignments`` has none, so its workflow state is fingerprinted directly.
    See ``_TABLE_CHANGE_COLUMN`` -- assuming ``updated_at`` on every table was a
    real bug that made in-place status transitions invisible to the UI.
  * The SSE endpoint streams this token every second; when it changes, the
    frontend refetches. If the SSE connection drops, the frontend falls back to
    POLLING the snapshot endpoint (same token), so status freshness degrades
    gracefully instead of freezing.
  * ``health()`` reports whether the streaming transport is expected to work so
    the client can decide up-front whether to even attempt SSE.

The token is intentionally opaque to the client — it only ever compares "did it
change?", never parses it.
"""
from __future__ import annotations

import hashlib
import logging
import re

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("dtc.status_stream")

# Tables we have already complained about (see _table_signature).
_SIGNATURE_FAILURES: set[str] = set()

# SECURITY: table names interpolated into raw SQL below come only from the fixed
# _STATUS_TABLES values (no user input), but _ident() enforces that invariant so
# a future change can't turn this into an injection point. Raises (NOT assert).
_SQL_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _ident(name: str) -> str:
    if not isinstance(name, str) or not _SQL_IDENT_RE.fullmatch(name):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return name


# logical name -> physical table. Only tables that carry a user-visible status.
#
# AUDIT 2026-08-01 (High): the "documents" key mapped to a table named
# "documents", which DOES NOT EXIST -- the generated-document model is backed by
# `generated_documents`. Because _table_signature() wraps its COUNT(*) in a bare
# `except Exception: return "0:none"`, the missing table produced a *constant*
# signature instead of an error. Consequences:
#   * generating, regenerating or deleting a document never changed the token,
#     so the SPA was never told to refetch -- document status looked frozen
#     until the user manually reloaded or some OTHER tracked table happened to
#     change at the same time (which is why it looked intermittent, not broken);
#   * the failure was invisible: no log line, no 500, and the SSE stream kept
#     happily emitting a token.
# The physical names are now pinned by tests/test_status_stream_tables.py, which
# resolves them against SQLAlchemy metadata so a future table rename cannot
# silently reintroduce a dead mapping.
_STATUS_TABLES = {
    "candidates": "candidates",
    "assignments": "candidate_assignments",
    "demands": "demands",
    "documents": "generated_documents",
}


def _table_signature(db: Session, table: str) -> str:
    """A per-table signature: '<count>:<max_updated_at>' (both cheap to query)."""
    table = _ident(table)
    try:
        count = db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0
    except Exception as exc:  # pragma: no cover - defensive
        # The swallowed exception here is what let the `documents` -> `documents`
        # mis-mapping survive unnoticed (see _STATUS_TABLES). Keep the graceful
        # degradation -- one bad table must not kill the SSE stream -- but never
        # again silently. Logged once per table because this runs at ~1 Hz per
        # connected client and would otherwise flood the log.
        if table not in _SIGNATURE_FAILURES:
            _SIGNATURE_FAILURES.add(table)
            log.warning(
                "status stream: table %r is unusable (%s) -- its signature is "
                "pinned at 0:none, so changes to it will NOT wake the UI",
                table, exc.__class__.__name__,
            )
        return "0:none"
    return f"{count}:{_change_marker(db, table)}"


# Physical table -> the column that moves when a row is edited, or None when the
# table has no such column.
#
# AUDIT 2026-08-01 (follow-on to the `documents` mis-mapping): the previous code
# ran `SELECT MAX(updated_at)` against every table behind `except Exception:
# pass`, and its comment asserted "updated_at exists on all status tables". That
# claim was FALSE for half of them:
#   * generated_documents has `generated_at`, not `updated_at`
#   * candidate_assignments has NO timestamp column at all
# Both silently degraded to a count-only signature, so the token moved only on
# INSERT/DELETE. The practical consequence was worse than the mis-mapped table
# name: `candidate_assignments.status` is the app's primary workflow state
# (pending -> documents_pending -> processing -> visa_issued -> deployed), and
# advancing it is an UPDATE with a stable row count -- the exact case a
# count-only signature cannot see. Users moving a candidate through the pipeline
# saw a stale list until they reloaded by hand.
#
# Deliberately NOT fixed by adding `updated_at` columns: that means a migration
# against live tenant databases, and SQLAlchemy's `onupdate` only fires for ORM
# writes (raw-SQL updates elsewhere in the codebase would not bump it), so the
# schema change would buy an incomplete guarantee at real upgrade risk. Instead
# each table declares how it can be observed, and tables with no timestamp are
# fingerprinted by their status histogram, which *does* change on a transition.
_TABLE_CHANGE_COLUMN: dict[str, str | None] = {
    "candidates": "updated_at",
    "demands": "updated_at",
    "generated_documents": "generated_at",
    "candidate_assignments": None,  # no timestamp -> status histogram, below
}


def _change_marker(db: Session, table: str) -> str:
    """Return a token that moves when any row in ``table`` is edited.

    Uses the declared timestamp column when the table has one. For a table with
    no timestamp, falls back to MAX(id) plus a digest of the status histogram so
    an in-place status transition is still detected.
    """
    col = _TABLE_CHANGE_COLUMN.get(table, "updated_at")
    if col:
        try:
            val = db.execute(text(f"SELECT MAX({_ident(col)}) FROM {table}")).scalar()
            return "none" if val is None else str(val)
        except Exception as exc:
            # A declared column that does not exist is a real defect (it is how
            # this bug hid for so long), so say so once instead of degrading
            # quietly to a count-only signature.
            key = f"{table}.{col}"
            if key not in _SIGNATURE_FAILURES:
                _SIGNATURE_FAILURES.add(key)
                log.warning(
                    "status stream: %s is not readable (%s) -- edits to %s will "
                    "not wake the UI until this column is corrected",
                    key, exc.__class__.__name__, table,
                )
            return "none"

    # No timestamp column: fingerprint the workflow state itself.
    try:
        max_id = db.execute(text(f"SELECT MAX(id) FROM {table}")).scalar() or 0
        rows = db.execute(
            text(f"SELECT status, COUNT(*) FROM {table} GROUP BY status")
        ).fetchall()
        # Sorted so the digest does not depend on the backend's group ordering.
        hist = ",".join(f"{r[0]}={r[1]}" for r in sorted(rows, key=lambda r: str(r[0])))
        return f"{max_id}|{hashlib.sha256(hist.encode('utf-8')).hexdigest()[:12]}"
    except Exception as exc:
        key = f"{table}.__histogram__"
        if key not in _SIGNATURE_FAILURES:
            _SIGNATURE_FAILURES.add(key)
            log.warning(
                "status stream: cannot fingerprint %r (%s) -- status edits there "
                "will not wake the UI", table, exc.__class__.__name__,
            )
        return "none"


def status_version(db: Session) -> str:
    """Opaque monotonic-ish token that changes whenever any tracked status row
    is inserted, deleted, or has its ``updated_at`` bumped by an edit."""
    parts = []
    for logical, table in _STATUS_TABLES.items():
        parts.append(f"{logical}={_table_signature(db, table)}")
    raw = "|".join(parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return digest


def status_snapshot(db: Session) -> dict:
    """Full snapshot the polling fallback returns: the version token plus a
    per-entity breakdown so the client can target which list to refetch."""
    entities = {}
    for logical, table in _STATUS_TABLES.items():
        table = _ident(table)
        try:
            count = db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0
        except Exception:
            count = 0
        entities[logical] = {
            "count": count,
            "signature": _table_signature(db, table),
        }
    return {
        "version": status_version(db),
        "entities": entities,
    }


def health() -> dict:
    """Realtime transport health. SSE is served by the same ASGI app, so if the
    client can reach this endpoint the stream transport is available. The client
    still keeps polling as a fallback in case an intermediary proxy buffers or
    kills long-lived SSE connections."""
    return {
        "ok": True,
        "transport": "sse",
        "fallback": "polling",
        "poll_interval_ms": 5000,
        "stream_path": "/api/realtime/status-stream",
        "snapshot_path": "/api/realtime/status-snapshot",
    }
