#!/usr/bin/env python3
"""
fix_template_geometry.py
========================

Root-cause fix for the "document coordinate engine has bugs / fields overlap
on the visa forms" complaint.

WHY FIELDS DRIFT
----------------
Both the WYSIWYG designer (document_customize.html) and the print/PDF renderer
(app/api/endpoints/documents.py) paint the background scan STRETCHED to exactly
fill the page box:

    designer:  <img #bgImg style="object-fit: fill">      -> 595 x page_height
    print/PDF: .doc-bg { background-size: 100% 100% }      -> page_w_mm x page_h_mm

That is internally consistent *only when the page box has the SAME aspect ratio
as the background image*. If a scan is (say) 1951x2339 (aspect 0.8341) but the
template is left at default A4 595x842 (aspect 0.7067), the image is squeezed
~15% vertically. Every horizontal form line then lands in the wrong place, so
the field tokens visually collide with the printed labels -- exactly the
Karachi visa form in the user's screenshots.

THE FIX (aspect-lock, visual-preserving)
-----------------------------------------
For each template that has a real background image, recompute page_height so the
page aspect ratio equals the image aspect ratio (keeping page_width fixed), then
proportionally rescale every field's y / height by (new_h / old_h) so each field
keeps its *visual* position on the form. Templates already matching (drift < 1%)
are left untouched.

This makes designer == print == PDF with ZERO distortion, and re-aligns the
fields that had drifted.

Usage:
    venv/bin/python scripts/fix_template_geometry.py            # apply to all tenant DBs + control DB
    venv/bin/python scripts/fix_template_geometry.py --dry-run  # show what would change
"""
import argparse
import glob
import os
import sqlite3
import sys

try:
    from PIL import Image
except Exception:  # pragma: no cover
    print("Pillow (PIL) is required: pip install pillow", file=sys.stderr)
    raise

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BG_DIR = os.path.join(HERE, "app", "static", "pdf_backgrounds")
DRIFT_THRESHOLD_PCT = 1.0  # only fix templates whose aspect drift exceeds this


def _bg_filename(background_image: str) -> str:
    return (background_image or "").strip().split("/")[-1]


def _image_aspect(fname: str):
    """Return width/height of the background image, or None if unavailable."""
    if not fname:
        return None
    path = os.path.join(BG_DIR, fname)
    if not os.path.exists(path):
        return None
    try:
        with Image.open(path) as im:
            w, h = im.size
        if w and h:
            return w / h
    except Exception:
        return None
    return None


def process_db(db_path: str, dry_run: bool) -> int:
    if not os.path.exists(db_path):
        return 0
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    cur = db.cursor()
    # Guard: table may not exist in every DB.
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='document_templates'")
    if not cur.fetchone():
        db.close()
        return 0

    cur.execute(
        "SELECT id, name, background_image, page_width, page_height FROM document_templates"
    )
    templates = [dict(r) for r in cur.fetchall()]
    changed = 0
    print(f"\n=== {db_path} ===")
    for t in templates:
        fname = _bg_filename(t["background_image"])
        iasp = _image_aspect(fname)
        pw = float(t["page_width"] or 595)
        ph = float(t["page_height"] or 842)
        if not iasp or ph <= 0 or pw <= 0:
            print(f"  [skip] #{t['id']:<3} {t['name'][:40]:<40} (no usable bg image)")
            continue
        pasp = pw / ph
        drift = abs(pasp - iasp) / iasp * 100.0
        new_h = round(pw / iasp, 1)
        if drift <= DRIFT_THRESHOLD_PCT or abs(new_h - ph) < 0.5:
            print(f"  [ok]   #{t['id']:<3} {t['name'][:40]:<40} {pw:.0f}x{ph:.0f} (drift {drift:.1f}%)")
            continue

        scale = new_h / ph
        # Rescale fields to preserve their visual position on the (now
        # undistorted) page. y and height both scale by new_h/old_h.
        cur.execute(
            "SELECT id, y, height FROM document_fields WHERE template_id=?",
            (t["id"],),
        )
        fields = [dict(r) for r in cur.fetchall()]
        print(
            f"  [FIX]  #{t['id']:<3} {t['name'][:40]:<40} "
            f"{pw:.0f}x{ph:.0f} -> {pw:.0f}x{new_h:.0f} "
            f"(drift {drift:.1f}%, scale {scale:.4f}, {len(fields)} fields)"
        )
        if dry_run:
            continue
        cur.execute(
            "UPDATE document_templates SET page_height=? WHERE id=?",
            (new_h, t["id"]),
        )
        for f in fields:
            new_y = round(float(f["y"] or 0) * scale, 2)
            new_fh = round(float(f["height"] or 0) * scale, 2) if f["height"] else f["height"]
            cur.execute(
                "UPDATE document_fields SET y=?, height=? WHERE id=?",
                (new_y, new_fh, f["id"]),
            )
        changed += 1

    if not dry_run and changed:
        db.commit()
    db.close()
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="show changes without writing")
    args = ap.parse_args()

    db_paths = [os.path.join(HERE, "data", "dogar_trading.db")]
    db_paths += sorted(glob.glob(os.path.join(HERE, "data", "tenants", "tenant_*.db")))

    total = 0
    for p in db_paths:
        total += process_db(p, args.dry_run)

    print(f"\n{'DRY-RUN: would fix' if args.dry_run else 'Fixed'} {total} template(s).")


if __name__ == "__main__":
    main()
