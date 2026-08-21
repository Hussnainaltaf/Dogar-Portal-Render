"""One-off migration (2026-07-11) — applies the owner-requested document fixes
to an ALREADY-seeded control + tenant database:

  1. Rename "HBL - Overseas Employment Corporation" → "NBP Form 32"
     (preserves the template's existing field coordinates / history).
  2. Retire the standalone "OEP Form Page 2" template — the OEP form is now a
     single 2-page document (front + back).
  3. Add page-2 fields to "OEP Form" (next-of-kin / job / employer) and to
     "NBP Deposit Slip" (Director-General + OPF copies) if missing.
  4. Write the designer meta "page_backgrounds" key so each multi-page template
     renders every page with its correct scanned-form background.

Idempotent: safe to run repeatedly. Run against every DB file:

    venv/bin/python scripts/migrate_docs_2026_07_11.py data/dogar_trading.db
    for f in data/tenants/*.db; do venv/bin/python scripts/migrate_docs_2026_07_11.py "$f"; done
"""
import json
import re
import sqlite3
import sys

FENCE_RE = re.compile(r"<!--designer:(.+?)-->", re.DOTALL)

# Page-2 field rows: (label, key, x, y, w, fs, bold, align, page)
OEP_P2 = [
    ("Next of Kin",  "next_of_kin_name",     150, 735, 300, 11, 0, "left", 2),
    ("Kin Relation", "next_of_kin_relation", 410, 735, 170, 11, 0, "left", 2),
    ("Job Title",    "profession",           150, 655, 250, 11, 0, "left", 2),
    ("Salary",       "salary",               410, 655, 170, 11, 0, "left", 2),
    ("Country",      "country",              150, 625, 250, 11, 0, "left", 2),
    ("Sponsor Name", "sponsor_name",         150, 595, 430, 11, 0, "left", 2),
    ("Passport No",  "passport_no",          150, 200, 250, 11, 0, "left", 2),
    ("Full Name",    "full_name",            150, 170, 300, 11, 1, "left", 2),
]
NBP_P2 = [
    ("Depositor Name", "full_name",   180, 700, 360, 11, 1, "left", 2),
    ("CNIC",           "cnic",        180, 670, 250, 11, 0, "left", 2),
    ("Phone",          "phone",       430, 670, 130, 11, 0, "left", 2),
    ("Address",        "address",     180, 640, 380, 10, 0, "left", 2),
    ("Passport No",    "passport_no", 180, 610, 250, 11, 0, "left", 2),
    ("Today's Date",   "__today__",   430, 610, 130, 11, 1, "left", 2),
]
PAGE_BG = {
    "OEP Form": {"2": "oep_form_p2.jpg"},
    "NBP Deposit Slip": {"2": "nbp_deposit_slip.jpg"},
}


def _tpl_id(cur, name):
    row = cur.execute("SELECT id FROM document_templates WHERE name=?", (name,)).fetchone()
    return row[0] if row else None


def _add_page2_fields(cur, tpl_id, rows):
    have = cur.execute(
        "SELECT COUNT(*) FROM document_fields WHERE template_id=? AND page=2", (tpl_id,)
    ).fetchone()[0]
    if have:
        return 0
    for (label, key, x, y, w, fs, bold, align, page) in rows:
        cur.execute(
            """INSERT INTO document_fields
               (template_id, label, field_key, field_type, x, y, width, height,
                font_size, font_bold, font_italic, color, align, page)
               VALUES (?,?,?, 'text', ?,?,?, 20, ?,?, 0, '#000000', ?, ?)""",
            (tpl_id, label, key, x, y, w, fs, bold, align, page),
        )
    return len(rows)


def _set_page_bg(cur, name, mapping):
    tid = _tpl_id(cur, name)
    if not tid:
        return
    row = cur.execute("SELECT description FROM document_templates WHERE id=?", (tid,)).fetchone()
    desc = row[0] or ""
    m = FENCE_RE.search(desc)
    meta = {}
    if m:
        try:
            meta = json.loads(m.group(1)) or {}
        except (ValueError, json.JSONDecodeError):
            meta = {}
    if meta.get("page_backgrounds") == mapping:
        return
    meta["page_backgrounds"] = mapping
    base = FENCE_RE.sub("", desc).strip()
    new_desc = (base + f"\n<!--designer:{json.dumps(meta)}-->").strip()
    cur.execute("UPDATE document_templates SET description=? WHERE id=?", (new_desc, tid))


def migrate(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    # Does this DB even have the document tables? (Tenant DBs might not.)
    t = cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='document_templates'"
    ).fetchone()
    if not t:
        print(f"  {db_path}: no document_templates table — skipped")
        conn.close()
        return

    # 1. Rename HBL → NBP Form 32
    hbl = _tpl_id(cur, "HBL - Overseas Employment Corporation")
    if hbl:
        clash = _tpl_id(cur, "NBP Form 32")
        if clash and clash != hbl:
            cur.execute("DELETE FROM document_fields WHERE template_id=?", (hbl,))
            cur.execute("DELETE FROM document_templates WHERE id=?", (hbl,))
            print(f"  {db_path}: removed stale HBL (NBP Form 32 already existed)")
        else:
            cur.execute(
                "UPDATE document_templates SET name=?, description=? WHERE id=?",
                ("NBP Form 32", "NBP Form 32 deposit slip", hbl),
            )
            print(f"  {db_path}: renamed HBL → NBP Form 32")

    # 2. Retire standalone OEP Form Page 2
    oep2 = _tpl_id(cur, "OEP Form Page 2")
    if oep2:
        cur.execute("DELETE FROM document_fields WHERE template_id=?", (oep2,))
        cur.execute("DELETE FROM document_templates WHERE id=?", (oep2,))
        print(f"  {db_path}: retired 'OEP Form Page 2' (folded into OEP Form)")

    # 3. Add page-2 fields
    oep = _tpl_id(cur, "OEP Form")
    if oep:
        n = _add_page2_fields(cur, oep, OEP_P2)
        if n:
            print(f"  {db_path}: added {n} page-2 fields to OEP Form")
    nbp = _tpl_id(cur, "NBP Deposit Slip")
    if nbp:
        n = _add_page2_fields(cur, nbp, NBP_P2)
        if n:
            print(f"  {db_path}: added {n} page-2 fields to NBP Deposit Slip")

    # 4. page_backgrounds meta
    for name, mapping in PAGE_BG.items():
        _set_page_bg(cur, name, mapping)

    conn.commit()
    conn.close()
    print(f"  {db_path}: ✓ done")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: migrate_docs_2026_07_11.py <db_path> [<db_path> ...]")
        sys.exit(1)
    for p in sys.argv[1:]:
        migrate(p)
