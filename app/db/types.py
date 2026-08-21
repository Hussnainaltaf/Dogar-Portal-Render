"""Shared SQLAlchemy column types.

FlexibleDate
------------
SQLite's ``Date`` type raises ``TypeError: SQLite Date type only accepts
Python date objects as input`` whenever an ISO string (the natural JSON
payload from the SPA, e.g. "2026-07-03") reaches the driver.  Historically
this caused scattered 500s across clients / candidates / demands endpoints,
each patched locally.

``FlexibleDate`` fixes the whole class of bug at the lowest layer: any
value bound to a date column is coerced to a ``datetime.date`` (accepting
date/datetime objects, ISO strings, dd/mm/yyyy, dd-mm-yyyy, mm/dd/yyyy),
and blank/unparseable input becomes NULL instead of a server error.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date
from sqlalchemy.types import TypeDecorator


def coerce_date(value):
    """Best-effort conversion of an incoming value to ``datetime.date``.

    Returns ``None`` for blank or unparseable input so a bad date never
    causes a 500.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "").replace("/", "-")).date()
    except (ValueError, TypeError):
        pass
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except (ValueError, TypeError):
            continue
    return None


class FlexibleDate(TypeDecorator):
    """A Date column that tolerates ISO / dd-mm-yyyy strings from JSON."""

    impl = Date
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return coerce_date(value)
