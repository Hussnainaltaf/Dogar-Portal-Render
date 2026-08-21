#!/usr/bin/env python3
"""Migrate a staged portal SQLite database into a durable target database.

The source database must already have been produced by the two legacy import
passes. This command copies every source table that also exists in the current
portal SQLAlchemy metadata, including candidates, demands, clients,
job_categories, candidate_assignments, and lookup tables.

Safety properties:
* dry-run is the default; ``--apply`` is required for writes;
* the target schema is initialized from the current portal metadata;
* inserts are conflict-safe on primary/unique keys and never overwrite rows;
* ``tenants`` is skipped for a tenant database and copied only for a control DB;
* row counts are verified after the copy;
* ``--force`` is intentionally not supported: use a separate database for a
  first migration and take a backup before any production change.

Examples:
    python scripts/migrate_staged_database.py \
        --source-sqlite /path/to/dogar_trading.db \
        --target-url postgresql+psycopg2://user:pass@host/db \
        --dry-run

    python scripts/migrate_staged_database.py \
        --source-sqlite /path/to/dogar_trading.db \
        --target-url postgresql+psycopg2://user:pass@host/db \
        --target-kind tenant --apply --verify
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Table, create_engine, func, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import app.models  # noqa: E402,F401
from app.db.session import Base  # noqa: E402

log = logging.getLogger("migrate_staged_database")
BATCH_SIZE = 500
SKIP_TABLES = {"alembic_version", "sqlite_sequence"}


def _safe_identifier(name: str) -> str:
    if not name.replace("_", "").isalnum() or not name[0].isalpha():
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return name


def _init_schema(engine: Engine) -> None:
    """Create missing tables using the current SQLAlchemy metadata.

    Production PostgreSQL databases should normally be upgraded with Alembic
    first. ``create_all`` is deliberately limited to missing objects and does
    not drop or alter existing tables.
    """
    Base.metadata.create_all(bind=engine)


def _source_tables(source_engine: Engine) -> set[str]:
    return set(inspect(source_engine).get_table_names())


def _target_tables(target_engine: Engine) -> set[str]:
    return set(inspect(target_engine).get_table_names())


def _insert_batch(target_engine: Engine, table: Table, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    dialect = target_engine.dialect.name
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    elif dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        raise RuntimeError(
            f"Unsupported target dialect {dialect!r}; use SQLite or PostgreSQL."
        )
    statement = insert(table).values(rows).on_conflict_do_nothing()
    with target_engine.begin() as conn:
        result = conn.execute(statement)
        return max(int(result.rowcount or 0), 0)


def _parse_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return None


def _coerce_row(table: Table, row: dict[str, Any]) -> dict[str, Any]:
    for column in table.columns:
        name = column.name
        value = row.get(name)
        type_name = str(column.type).lower()
        if value is None:
            continue
        if isinstance(value, str) and ("datetime" in type_name or "timestamp" in type_name):
            parsed = _parse_datetime(value)
            if parsed is not None:
                row[name] = parsed
        elif isinstance(value, str) and type_name == "date":
            parsed = _parse_datetime(value)
            if parsed is not None:
                row[name] = parsed.date()
        elif type_name.startswith("bool") and isinstance(value, int) and value in (0, 1):
            row[name] = bool(value)
    return row


def _copy_table(
    source_engine: Engine,
    target_engine: Engine,
    table: Table,
    *,
    verify: bool,
) -> dict[str, int | str]:
    source_inspector = inspect(source_engine)
    source_columns = {
        column["name"] for column in source_inspector.get_columns(table.name)
    }
    columns = [column.name for column in table.columns if column.name in source_columns]
    if not columns:
        return {"source": 0, "inserted": 0, "target": 0, "status": "skipped-no-columns"}

    table_name = _safe_identifier(table.name)
    quoted_columns = ", ".join(f'"{_safe_identifier(name)}"' for name in columns)
    source_count = 0
    inserted_count = 0
    with source_engine.connect() as source_conn:
        result = source_conn.execution_options(stream_results=True).execute(
            text(f'SELECT {quoted_columns} FROM "{table_name}"')
        )
        batch: list[dict[str, Any]] = []
        for source_row in result:
            batch.append(_coerce_row(table, dict(zip(columns, source_row, strict=False))))
            source_count += 1
            if len(batch) >= BATCH_SIZE:
                inserted_count += _insert_batch(target_engine, table, batch)
                batch.clear()
        inserted_count += _insert_batch(target_engine, table, batch)

    target_count = 0
    if verify:
        with target_engine.connect() as target_conn:
            target_count = int(target_conn.execute(select(func.count()).select_from(table)).scalar_one())
    status = "ok" if target_count >= source_count else "incomplete"
    return {
        "source": source_count,
        "inserted": inserted_count,
        "target": target_count,
        "status": status,
    }


def migrate(
    source_path: Path,
    target_url: str,
    *,
    target_kind: str,
    apply: bool,
    verify: bool,
) -> dict[str, dict[str, int | str]]:
    if not source_path.exists():
        raise FileNotFoundError(f"Source SQLite database not found: {source_path}")
    if not target_url.startswith(("sqlite://", "postgresql://", "postgresql+psycopg2://")):
        raise ValueError("Target URL must use sqlite:// or postgresql://")
    if target_kind not in {"single", "control", "tenant"}:
        raise ValueError("target-kind must be single, control, or tenant")

    source_engine = create_engine(
        f"sqlite:///{source_path}", connect_args={"check_same_thread": False}
    )
    target_engine = create_engine(target_url, pool_pre_ping=True)
    try:
        source_names = _source_tables(source_engine)
        target_names = _target_tables(target_engine)
        expected = [
            table
            for table in Base.metadata.sorted_tables
            if table.name not in SKIP_TABLES
            and not (target_kind == "tenant" and table.name == "tenants")
        ]
        missing_source = [table.name for table in expected if table.name not in source_names]
        print(json.dumps({
            "source": str(source_path),
            "target_dialect": target_engine.dialect.name,
            "target_kind": target_kind,
            "apply": apply,
            "source_tables": sorted(source_names),
            "target_tables_before": sorted(target_names),
            "missing_source_tables": missing_source,
            "planned_tables": [table.name for table in expected if table.name in source_names],
        }, indent=2))
        if not apply:
            print("DRY RUN: no schema or data changes performed.")
            return {}

        _init_schema(target_engine)
        results: dict[str, dict[str, int | str]] = {}
        for table in expected:
            if table.name not in source_names:
                continue
            result = _copy_table(source_engine, target_engine, table, verify=verify)
            results[table.name] = result
            log.info(
                "%s: source=%s inserted=%s target=%s status=%s",
                table.name,
                result["source"],
                result["inserted"],
                result["target"],
                result["status"],
            )
        if verify:
            incomplete = [name for name, result in results.items() if result["status"] != "ok"]
            if incomplete:
                raise RuntimeError(f"Migration verification incomplete for: {', '.join(incomplete)}")
        print(json.dumps({"results": results}, indent=2))
        return results
    finally:
        source_engine.dispose()
        target_engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sqlite", required=True, type=Path)
    parser.add_argument("--target-url", required=True)
    parser.add_argument("--target-kind", choices=("single", "control", "tenant"), default="single")
    parser.add_argument("--apply", action="store_true", help="Perform writes; without this flag the command is a dry run.")
    parser.add_argument("--verify", action="store_true", help="Count source and target rows after copying.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        migrate(
            args.source_sqlite,
            args.target_url,
            target_kind=args.target_kind,
            apply=args.apply,
            verify=args.verify,
        )
    except (FileNotFoundError, ValueError, SQLAlchemyError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
