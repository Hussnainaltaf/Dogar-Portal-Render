#!/usr/bin/env python3
"""Export verified PDF template geometry to app/data/template_geometry.json.

WHY THIS SCRIPT EXISTS
----------------------
Defect found 2026-08-03 (deep audit): the ONLY place the measured, print-
verified field coordinates existed was the operator's own
``data/dogar_trading.db``. That file is deliberately never shipped
(scripts/make_release_zip.sh excludes it -- it holds real candidate PII) and
``data/`` is gitignored. So every fresh install fell back to
``scripts/seed_document_fields.py``, whose own docstring describes its numbers
as "best-effort approximations".

Measured consequence on a fresh install, before the fix:
  * 13 fields did not exist at all (customer_address, customer_oep_license,
    customer_emigrant_office, plus 10 Karachi visa fields)
  * 32 fields sat up to 426 pt from their verified position
  * templates 1/7/8 came up A4 instead of the verified Letter
  * ``cnic`` came up plain ``text`` instead of ``char_cells``, losing the
    digit-grid geometry fixed in revision 6e0a4b2d8f37

Alembic could not rescue this: the geometry revisions are UPDATE / guarded
INSERT statements against rows the *seeder* creates, so on a fresh DB they
correctly match zero rows.

This script makes the shipped JSON snapshot the single source of truth. Run it
after adjusting coordinates in the designer so the fix reaches new deployments
instead of living only in one operator's database.

Usage:
    venv/bin/python scripts/export_template_geometry.py            # write
    venv/bin/python scripts/export_template_geometry.py --check    # verify only
    venv/bin/python scripts/export_template_geometry.py --db PATH  # other DB

``--check`` exits non-zero when the snapshot is stale, so CI / the release gate
can refuse to ship a build whose shipped geometry has drifted from the DB it
was measured against.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(REPO_ROOT, "data", "dogar_trading.db")
OUT_FILE = os.path.join(REPO_ROOT, "app", "data", "template_geometry.json")

# Rounded to 2dp: the measurements themselves come from a 300dpi raster
# (0.24 pt per pixel), so more precision than this is false precision.
COORD_PRECISION = 2


def _load_meta(raw):
    """meta is a JSON column: SQLite hands back a str, Postgres a dict."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def export(db_path: str) -> dict:
    if not os.path.isfile(db_path):
        raise SystemExit(f"database not found: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        out: dict = {}
        for tpl in conn.execute(
            "SELECT * FROM document_templates ORDER BY id"
        ):
            fields = []
            for f in conn.execute(
                "SELECT * FROM document_fields WHERE template_id = ? ORDER BY id",
                (tpl["id"],),
            ):
                meta = _load_meta(f["meta"])
                row = {
                    "label": f["label"],
                    "field_key": f["field_key"],
                    "field_type": f["field_type"] or "text",
                    "static_value": f["static_value"] or "",
                    "x": round(f["x"], COORD_PRECISION),
                    "y": round(f["y"], COORD_PRECISION),
                    "width": round(f["width"], COORD_PRECISION),
                    "height": round(f["height"], COORD_PRECISION),
                    "font_size": round(f["font_size"], COORD_PRECISION),
                    "font_bold": bool(f["font_bold"]),
                    "font_italic": bool(f["font_italic"]),
                    "color": f["color"] or "#000000",
                    "align": f["align"] or "left",
                    "page": int(f["page"] or 1),
                }
                # Only emit meta when non-empty so the diff stays readable.
                if meta:
                    row["meta"] = meta
                fields.append(row)
            out[tpl["name"]] = {
                "page_width": round(tpl["page_width"], COORD_PRECISION),
                "page_height": round(tpl["page_height"], COORD_PRECISION),
                "page_size": tpl["page_size"],
                "orientation": tpl["orientation"],
                "fields": fields,
            }
        return out
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB, help="source database")
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if the shipped snapshot is stale")
    args = ap.parse_args()

    data = export(args.db)
    rendered = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    n_tpl = len(data)
    n_fld = sum(len(v["fields"]) for v in data.values())

    if args.check:
        try:
            with open(OUT_FILE, encoding="utf-8") as fh:
                current = fh.read()
        except FileNotFoundError:
            print(f"STALE: {OUT_FILE} does not exist", file=sys.stderr)
            return 1
        if current != rendered:
            print(
                f"STALE: {OUT_FILE} differs from {args.db}. "
                f"Re-run without --check to refresh it.",
                file=sys.stderr,
            )
            return 1
        print(f"OK: snapshot matches ({n_tpl} templates, {n_fld} fields)")
        return 0

    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as fh:
        fh.write(rendered)
    print(f"wrote {OUT_FILE}: {n_tpl} templates, {n_fld} fields")
    for name, tpl in data.items():
        print(f"  {name[:44]:44} {tpl['page_size']:7} {len(tpl['fields']):>3} fields")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
