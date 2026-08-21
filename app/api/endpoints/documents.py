import base64 as _b64_top
import io
import logging
import mimetypes as _mt_top
import os
import uuid
from datetime import date, datetime
from functools import lru_cache
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.core.geometry_contract import ASCENT_RATIO, effective_height
from app.core.permissions import require_permission
from app.core.tenancy import get_tenant_db
from app.core.uploads import InvalidImage, normalize_image
from app.core.workflow import AuditAction
from app.models import Candidate, Client, Demand, DocumentField, DocumentTemplate, GeneratedDocument, User
from app.schemas.schemas import (
    DocumentFieldCreate,
    DocumentFieldOut,
    DocumentTemplateCreate,
    DocumentTemplateOut,
    DocumentTemplateUpdate,
)
from app.services.audit import log_document_action
from app.services.pdf_engine import generate_pdf, get_available_fields

# AUDIT FIX (2026-07-07): module-level structured logger (matches the
# "dtc.<module>" convention already used inline elsewhere in this file).
# Replaces the bare debug print() calls in the exception handlers below —
# exception-handling logic is unchanged, only the reporting call now goes
# through the logging framework.
log = logging.getLogger("dtc.documents")

router = APIRouter()


# ============================================================================
# DATA OVERLAY ENGINE
# ============================================================================
# Maps record column values to template field coordinates so the frontend can
# render an exact-pixel preview of the printed document (background image +
# data text positioned at the configured coordinates). The same data is also
# used by the PDF engine for offline export.

# Designer/PDF font name -> browser CSS font stack, so the print/HTML preview
# renders the SAME face the designer canvas and PDF use. CSS cannot resolve
# ReportLab identifiers (e.g. "Times-Roman", "DejaVuSans"), so those legacy
# values are mapped too. Keep in sync with cssFontStack() in the frontend
# app/frontend/src/pages/designer/types.ts.
_CSS_FONT_STACKS = {
    "arial": "Arial, Helvetica, 'Liberation Sans', sans-serif",
    "helvetica": "Helvetica, 'Helvetica Neue', Arial, sans-serif",
    "helvetica-bold": "Helvetica, Arial, sans-serif",
    "times new roman": "'Times New Roman', Times, 'Liberation Serif', serif",
    "times-roman": "'Times New Roman', Times, serif",
    "times-bold": "'Times New Roman', Times, serif",
    "georgia": "Georgia, 'Times New Roman', serif",
    "verdana": "Verdana, Geneva, 'DejaVu Sans', sans-serif",
    "tahoma": "Tahoma, Verdana, Geneva, sans-serif",
    "courier new": "'Courier New', Courier, 'Liberation Mono', monospace",
    "courier": "'Courier New', Courier, monospace",
    "courier-bold": "'Courier New', Courier, monospace",
    "noto arabic": "'Noto Sans Arabic', 'Noto Naskh Arabic', Tahoma, sans-serif",
    "notosansarabic": "'Noto Sans Arabic', 'Noto Naskh Arabic', Tahoma, sans-serif",
    "dejavu sans": "'DejaVu Sans', Verdana, sans-serif",
    "dejavusans": "'DejaVu Sans', Verdana, sans-serif",
}


def _css_font_stack(font_name: str) -> str:
    """Resolve a stored font name to a safe browser CSS font-family stack."""
    raw = str(font_name or "").replace(";", "").strip()
    if not raw:
        return "'Inter', 'Segoe UI', Arial, Helvetica, sans-serif"
    hit = _CSS_FONT_STACKS.get(raw.lower())
    if hit:
        return hit
    # Already looks like a CSS stack (contains a comma) → trust it; else quote.
    if "," in raw:
        return raw
    return f"'{raw}', 'Inter', Arial, Helvetica, sans-serif"


def _resolve_field_value(record, field_key: str) -> str:
    """Resolve a single field key against an ORM record.
    Supports system tokens (__today__, __now__) and dot-access for related
    fields (e.g. demand.sponsor_name).
    """
    if field_key == "__today__":
        return datetime.now().strftime("%d-%m-%Y")
    if field_key == "__now__":
        return datetime.now().strftime("%d-%m-%Y %H:%M")
    if record is None:
        return ""
    val = record
    for part in field_key.split("."):
        if val is None:
            return ""
        val = getattr(val, part, None)
    if val is None:
        return ""
    if isinstance(val, (datetime, date)):
        return val.strftime("%d-%m-%Y")
    return str(val)


def _load_record(db: Session, data_source: str, record_id: Optional[int]):
    if not record_id:
        return None
    mapping = {
        "candidate": Candidate,
        "demand": Demand,
        "client": Client,
        # "agent" removed 2026-07-30 with the Sub-Agents feature.
    }
    model = mapping.get(data_source)
    if not model:
        return None
    return db.query(model).filter(model.id == record_id).first()


def _grid_slot_span(count: int, group_sizes: Optional[list] = None) -> int:
    """How many *slot positions* wide a char_cells grid really is.

    Usually this equals ``count`` -- one slot per character. But a grouped
    grid (a CNIC printed 5-7-1) also consumes one blank slot per separator, so
    13 characters occupy 15 slots.

    GROUP-BLINDNESS BUG (audit 2026-08-11, second pass)
    ---------------------------------------------------
    Every pitch calculation here used to assume ``count`` slots. On Allied
    Bank "cnic" -- the one template that is correctly configured with
    ``group_sizes=[5, 7, 1]`` -- that under-measured the grid by two whole
    cells: the real span is 15 x 14.72 = 220.80pt, exactly the 220.8pt box,
    but the maths said 191.36pt. That made the fit test a false negative (it
    could not see a grouped grid overflowing) and, worse, made the *repair*
    wrong: refitting such a field would have divided the width by 13 instead
    of 15 and pushed every digit after the first dash out of its printed box.

    Counting slots instead of characters keeps both the check and the repair
    honest for grouped and ungrouped grids alike.

    DELEGATED (audit 2026-08-14): the implementation now lives in
    ``pdf_engine.grid_slot_span`` so the PDF engine, this HTML path and the save
    guard all measure a grid identically. This wrapper is kept because the
    module's own call sites and tests reference it by name.
    """
    from app.services.pdf_engine import grid_slot_span
    return grid_slot_span(count, group_sizes)


def _fitted_cell_pitch(width: float, count: int, gap: float,
                       stored: float,
                       group_sizes: Optional[list] = None) -> tuple[float, bool]:
    """Resolve the per-cell pitch for a char_cells field.

    Returns ``(pitch, was_clamped)``.

    DELEGATED (audit 2026-08-14). This clamp used to live only here, and the PDF
    engine obeyed ``meta["cell_width"]`` raw -- so on the real corruption found
    in this database (OEP Form ``phone``: 13 cells x 31.47pt = 409.11pt of cells
    inside a 99.60pt box) the two renderers of ONE template disagreed by 4.1x.
    This path kept the digits inside the box; the PDF sprayed them across the
    form and off the page. It went unnoticed because the shipped templates are
    clean: the divergence only appears once a field is resized in the designer,
    which is ordinary use.

    The single implementation is ``pdf_engine.fitted_cell_pitch`` -- see its
    docstring for the clamp rule and the 1.0pt tolerance. This wrapper stays
    because this module's call sites and tests reference it by name.
    """
    from app.services.pdf_engine import fitted_cell_pitch
    return fitted_cell_pitch(width, count, gap, stored, group_sizes)


def _sanitize_char_cell_meta(row: dict) -> Optional[str]:
    """Keep a char_cells field's cell pitch physically possible on SAVE.

    ROOT CAUSE (audit 2026-08-11)
    -----------------------------
    The render paths were clamping an impossible ``cell_width`` at print time,
    which stopped the *symptom* but let the bad number persist and reappear in
    the designer. The real defect is on the WRITE path, and it is reachable
    through ordinary use:

      * ``cell_width`` / ``cell_count`` are free-typed numbers in the
        properties panel, validated against nothing.
      * Resizing a field's box (or duplicating a field onto a smaller box)
        changes ``width`` but leaves ``meta.cell_width`` untouched, so the
        declared grid silently outgrows its box.

    That is exactly how OEP Form ``phone`` ended up declaring 13 x 31.47pt =
    409pt of cells inside a 99.6pt box -- the 'address' pitch left behind
    after the box was moved and shrunk. The value is stored, so the PDF and
    HTML engines disagree with the designer preview and every operator sees
    digits sprayed across neighbouring fields.

    Fixing it here -- in the single endpoint every client saves through --
    means the invariant holds no matter which UI (or script, or future mobile
    client) does the writing.

    Mutates ``row['meta']`` in place. Returns a human-readable note when a
    correction was applied, else ``None``.
    """
    if row.get("field_type") != "char_cells":
        return None
    meta = row.get("meta")
    if not isinstance(meta, dict):
        return None

    try:
        count = int(float(meta.get("cell_count") or 0))
        gap = float(meta.get("cell_gap") or 0)
        stored = float(meta.get("cell_width") or 0)
        width = float(row.get("width") or 0)
    except (TypeError, ValueError):
        return None

    if count <= 0 or width <= 0 or stored <= 0:
        return None

    # Measure the grid by the slots it really occupies: a grouped grid (CNIC
    # 5-7-1) also spends a blank slot on each separator, so 13 characters are
    # 15 slots wide. Measuring by `count` under-measures such a field and
    # would refit it to a pitch that walks every digit after the first dash
    # out of its pre-printed box.
    slots = _grid_slot_span(count, meta.get("group_sizes"))
    if slots <= 0:
        return None

    needed = stored * slots + gap * max(0, slots - 1)
    if needed <= width + 1.0:
        return None

    fitted, _ = _fitted_cell_pitch(width=width, count=count, gap=gap,
                                   stored=stored,
                                   group_sizes=meta.get("group_sizes"))
    meta["cell_width"] = round(fitted, 2)
    grid = f"{count} cells" if slots == count else f"{count} cells in {slots} slots"
    return (
        f"{row.get('field_key') or 'field'}: cell_width {stored:g}pt x {grid} "
        f"(+{gap:g}pt gaps) needs {needed:.1f}pt but the field box is only "
        f"{width:.1f}pt wide -- refitted to {meta['cell_width']:g}pt so the "
        f"characters stay inside their boxes"
    )


def _build_overlay_payload(tpl: DocumentTemplate, record, db: Optional[Session] = None) -> dict:
    """Build the JSON payload the frontend needs to render the data overlay
    on top of the background image. Coordinates are returned as stored
    (PDF-points, origin = bottom-left). The frontend converts to CSS
    coordinates (origin = top-left).
    """
    from app.services.overlay_engine import resolve_all_related_data_for_record, validate_overlay_mappings
    
    resolved_data = {}
    if db and record:
        resolved_data = resolve_all_related_data_for_record(db, tpl.data_source, record.id)

    fields_out = []
    for f in tpl.fields:
        key = f.field_key
        if key in resolved_data:
            val = resolved_data[key]
        else:
            val = _resolve_field_value(record, key)

        meta = f.meta or {}
        if isinstance(meta, str):
            try:
                import json as _json
                meta = _json.loads(meta)
            except Exception:
                meta = {}
        # meta is now guaranteed to be a dict — all subsequent accesses use it directly

        if f.field_type == "static":
            value = f.static_value or val
        elif f.field_type == "checkbox":
            value = "X" if val else ""
        elif f.field_type == "barcode":
            # Resolve the encoded value from the barcode-content merge spec
            # (matches the PDF engine) so the on-screen preview shows the
            # exact same number that prints under the bars.
            from app.services.pdf_engine import _resolve_merge_text
            spec = (meta.get("barcode_content") or meta.get("content")
                    or f.static_value or (("{{" + key + "}}") if key and not key.startswith("__") else ""))
            value = _resolve_merge_text(spec, resolved_data, record) if spec else (val or "")
        else:
            value = val

        # For trade_table fields, resolve the actual rows so the print HTML
        # can render the same table the PDF engine draws (audit Fix 4).
        trade_rows: list = []
        if f.field_type == "trade_table" and db is not None:
            from app.services.overlay_engine import resolve_trade_table_rows
            # Resolve the demand_id from the record (could be the demand
            # itself, or a candidate's assigned demand).
            demand_id = None
            if record is not None:
                demand_id = getattr(record, "demand_id", None) or getattr(record, "id", None) if tpl.data_source == "demand" else getattr(record, "demand_id", None)
            trade_rows = resolve_trade_table_rows(db, demand_id)

        # For barcodes, pre-render a crisp PNG/SVG (data-URI) the browser can show.
        # Route through _cached_barcode_svg so repeated calls for the same candidate
        # + field configuration are served from the in-process LRU cache instead of
        # re-running the full SVG-render pipeline on every overlay payload build.
        barcode_img = ""
        if f.field_type == "barcode":
            try:
                from app.services.pdf_engine import _cached_barcode_svg
                barcode_img = _cached_barcode_svg(
                    str(value or ""),
                    str(meta.get("symbology") or meta.get("format") or "code128"),
                    float(meta.get("bar_height") or 0.0),
                    float(meta.get("bar_width") or 0.0),
                    bool(meta.get("show_text", meta.get("human_readable", True))),
                    float(meta.get("caption_font_size") or meta.get("caption_font") or 8.0),
                    float(getattr(f, "width", 0) or 0),
                    float(getattr(f, "height", 0) or 0),
                )
            except Exception as _e:
                log.warning("barcode preview render failed: %s", _e)

        fields_out.append({
            "id": f.id,
            "label": f.label,
            "field_key": key,
            "field_type": f.field_type,
            "static_value": f.static_value or "",
            "value": value,
            "barcode_img": barcode_img,
            "trade_rows": trade_rows,
            "x": float(f.x or 0),
            "y": float(f.y or 0),
            "width": float(f.width or 200),
            "height": float(f.height or 20),
            "font_size": float(f.font_size or 11),
            "font_bold": bool(f.font_bold),
            "font_italic": bool(f.font_italic),
            "color": f.color or "#000000",
            "align": f.align or "left",
            "page": int(f.page or 1),
            "meta": meta,
        })

    # ------------------------------------------------------------------
    # ADDRESS CAPACITY POLICY for the designer / customize preview.
    # ------------------------------------------------------------------
    # The preview is the THIRD renderer of the same template (after the PDF
    # engine and the HTML print path) and it must not disagree with them. The
    # tempting shortcut -- reimplementing the abbreviate/truncate ladder in
    # TypeScript inside FieldMarker.tsx -- is exactly how the three paths
    # diverged before: the designer drew combs as one continuous string until
    # 2026-08-12 while the PDF engine drew separate cells, and nobody noticed
    # because each implementation was internally consistent.
    #
    # So the decision is made HERE, once, by the same function the other two
    # renderers call, and shipped to the browser as data. FieldMarker only
    # renders `comb_row`; it does not decide anything. A policy change cannot
    # reach two of three renderers.
    #
    # Done as a second pass because capacity depends on the SIBLING rows of the
    # same key (OEP Address is field[4] + field[24] with char_offset=13), which
    # are only all known once the first loop has finished.
    _has_combs = any(_fo.get("field_type") == "char_cells" for _fo in fields_out)
    if _has_combs:
        from app.services.comb_capacity import resolve_comb_row
        for _fo in fields_out:
            if _fo.get("field_type") != "char_cells":
                continue
            try:
                _row, _fit = resolve_comb_row(
                    _fo.get("value"), _fo.get("meta") or {},
                    sibling_fields=fields_out,
                    field_key=_fo.get("field_key") or "",
                    page=int(_fo.get("page") or 1),
                )
            except Exception as _ce:
                # A preview must still render if the policy hits a malformed
                # template: log it and fall back to the raw value rather than
                # 500-ing the whole customize screen.
                log.warning("comb capacity preview failed for %r: %s",
                            _fo.get("field_key"), _ce)
                continue
            #: exactly the characters this row prints -- FieldMarker uses this
            #: verbatim instead of slicing the value itself.
            _fo["comb_row"] = _row
            #: the whole-value decision (status / capacity / dropped text /
            #: human-readable reason), identical on every row of the same value.
            #: Drives the review-required badge in the designer.
            _fo["comb_fit"] = _fit.to_dict()

    # background_image can be stored either as a bare filename (e.g.
    # "allied_bank_form7.jpg") or as an already-rooted URL path
    # ("/static/pdf_backgrounds/allied_bank_form7.jpg"). Normalize both.
    bg_raw = (tpl.background_image or "").strip()
    if not bg_raw:
        bg_url = ""
    elif bg_raw.startswith("/") or bg_raw.startswith("http"):
        bg_url = bg_raw
    else:
        bg_url = f"/static/pdf_backgrounds/{bg_raw}"

    missing_required = []
    if resolved_data:
        missing_required = validate_overlay_mappings(tpl.fields, resolved_data)

    return {
        "template": {
            "id": tpl.id,
            "name": tpl.name,
            "data_source": tpl.data_source,
            "background_image": bg_raw,
            "background_url": bg_url,
            "page_width": float(tpl.page_width or 595),
            "page_height": float(tpl.page_height or 842),
        },
        "fields": fields_out,
        "missing_required_fields": missing_required,
    }


@router.get("/templates", response_model=List[DocumentTemplateOut])
def list_templates(
    db: Session = Depends(get_tenant_db), user: User = Depends(require_permission("documents:view")),
    category: Optional[str] = Query(None, description="Filter by category: permission | protector | visa_process"),
    data_source: Optional[str] = Query(None, description="Filter by data_source: demand | candidate | client | agent"),
    embassy: Optional[str] = Query(None, description="Embassy-aware filter: 'Karachi Embassy' / 'Islamabad Embassy'. "
                                   "Drops embassy-specific visa forms belonging to the OTHER embassy."),
):
    """List all PDF data-overlay templates. The portal does NOT generate
    documents from scratch -- every template uses a real-image background
    (visa form, bank slip, OEP form, etc.) and overlays coordinate-mapped
    fields from the linked record (Candidate / Demand / Client).

    Embassy-based visa form availability: when ?embassy= is provided,
    embassy-specific templates (names containing "Karachi" / "Islamabad")
    that belong to the OTHER embassy are excluded. Generic templates
    (no embassy in the name) are always kept. This mirrors the business
    rule: a demand filed at the Karachi embassy can only use the Karachi
    visa application form, and vice versa.
    """
    q = db.query(DocumentTemplate).options(joinedload(DocumentTemplate.fields))
    if category:
        q = q.filter(DocumentTemplate.category == category)
    if data_source:
        q = q.filter(DocumentTemplate.data_source == data_source)
    rows = q.order_by(DocumentTemplate.name.asc()).all()

    if embassy:
        emb_low = embassy.lower()
        mine = "karachi" if "karachi" in emb_low or "khi" in emb_low else (
               "islamabad" if "islamabad" in emb_low or "isb" in emb_low else "")
        if mine:
            other = "islamabad" if mine == "karachi" else "karachi"
            rows = [t for t in rows if other not in (t.name or "").lower()]
            # Matching-embassy forms first, generic forms after
            rows.sort(key=lambda t: 0 if mine in (t.name or "").lower() else 1)
    return rows


@router.post("/templates", response_model=DocumentTemplateOut)
def create_template(payload: DocumentTemplateCreate, db: Session = Depends(get_tenant_db), user: User = Depends(require_permission("documents:edit"))):
    obj = DocumentTemplate(**payload.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.get("/templates/{tpl_id}", response_model=DocumentTemplateOut)
def get_template(tpl_id: int, request: Request, db: Session = Depends(get_tenant_db), user: User = Depends(require_permission("documents:view"))):
    obj = db.query(DocumentTemplate).options(joinedload(DocumentTemplate.fields)).filter(DocumentTemplate.id == tpl_id).first()
    if not obj:
        raise HTTPException(404, "Template not found")
    log_document_action(
        db, action=AuditAction.VIEW.value, template=obj, actor=user,
        request=request, detail="template definition",
    )
    return obj


def _normalize_bg_filename(val: str) -> str:
    """Reduce any background_image value to a bare, safe filename so the canvas
    renderer can prepend /static/pdf_backgrounds/ exactly once. Handles legacy
    rows where the UI saved the full URL path.

    SECURITY (audit H-1, 2026-07-10): the previous implementation returned
    ``v.lstrip("/")``, which left path-traversal sequences (``../``) and nested
    subpaths intact. A crafted ``background_image`` could therefore escape the
    pdf_backgrounds directory when later joined with ``PDF_BG_DIR`` (e.g. the
    stale-file delete in upload_background, or the pdf_engine resolver). We now
    collapse the value to ``os.path.basename`` so only a leaf filename can ever
    be persisted or resolved — traversal is impossible by construction.
    """
    if not val:
        return val
    v = val.strip().replace("\\", "/")
    # Strip any /static/pdf_backgrounds/ prefix (with or without leading slash)
    for prefix in ("/static/pdf_backgrounds/", "static/pdf_backgrounds/"):
        if v.startswith(prefix):
            v = v[len(prefix):]
            break
    # Collapse to a bare filename: defeats "../", absolute paths and nested
    # subpaths. os.path.basename on a normalised (forward-slash) string returns
    # only the final path component.
    v = v.rsplit("/", 1)[-1]
    v = os.path.basename(v)
    # Reject anything that still looks like a traversal token.
    if v in ("", ".", ".."):
        return ""
    return v


def _safe_bg_path(filename: str) -> Optional[str]:
    """Resolve *filename* inside PDF_BG_DIR and guarantee the result stays within
    that directory (defence-in-depth for audit H-1). Returns the absolute path,
    or ``None`` if the name is empty or would escape the backgrounds directory.
    """
    if not filename:
        return None
    safe_name = _normalize_bg_filename(filename)
    if not safe_name:
        return None
    base = os.path.realpath(settings.PDF_BG_DIR)
    candidate = os.path.realpath(os.path.join(base, safe_name))
    # Must live directly under (or within) the backgrounds dir.
    if candidate != base and not candidate.startswith(base + os.sep):
        return None
    return candidate


@router.put("/templates/{tpl_id}", response_model=DocumentTemplateOut)
def update_template(tpl_id: int, payload: DocumentTemplateUpdate, request: Request, db: Session = Depends(get_tenant_db), user: User = Depends(require_permission("documents:edit"))):
    obj = db.query(DocumentTemplate).filter(DocumentTemplate.id == tpl_id).first()
    if not obj:
        raise HTTPException(404, "Template not found")
    data = payload.model_dump(exclude_unset=True)
    if "background_image" in data and data["background_image"]:
        data["background_image"] = _normalize_bg_filename(data["background_image"])

    # AUDIT (2026-08-15): snapshot BEFORE mutating, so the trail records what
    # actually changed rather than the post-write state twice.
    was_active = bool(obj.is_active)
    before_snapshot = {k: getattr(obj, k, None) for k in data}

    for k, v in data.items():
        setattr(obj, k, v)
    db.commit()
    db.refresh(obj)

    # Enable/disable is a distinct role-protected capability, so it gets its
    # own action rather than being buried inside a generic "update".
    now_active = bool(obj.is_active)
    if "is_active" in data and now_active != was_active:
        action = AuditAction.ENABLE.value if now_active else AuditAction.DISABLE.value
    else:
        action = AuditAction.UPDATE.value
    log_document_action(
        db, action=action, template=obj, actor=user, request=request,
        detail=", ".join(sorted(data.keys())),
        before=before_snapshot,
        after={k: getattr(obj, k, None) for k in data},
    )
    return obj


@router.delete("/templates/{tpl_id}")
def delete_template(tpl_id: int, request: Request, db: Session = Depends(get_tenant_db), user: User = Depends(require_permission("documents:edit"))):
    obj = db.query(DocumentTemplate).filter(DocumentTemplate.id == tpl_id).first()
    if not obj:
        raise HTTPException(404, "Template not found")
    # AUDIT: write the trail BEFORE the row disappears -- afterwards the
    # template name and category are unrecoverable.
    log_document_action(
        db, action=AuditAction.DELETE.value, template=obj, actor=user,
        request=request, before=obj,
    )
    db.delete(obj)
    db.commit()
    return {"ok": True}


@router.post("/templates/{tpl_id}/background")
async def upload_background(tpl_id: int, file: UploadFile = File(...), db: Session = Depends(get_tenant_db), user: User = Depends(require_permission("documents:edit"))):
    obj = db.query(DocumentTemplate).filter(DocumentTemplate.id == tpl_id).first()
    if not obj:
        raise HTTPException(404, "Template not found")

    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(400, "Background image too large (max 20 MB)")
    if len(content) < 256:
        raise HTTPException(400, "Background image too small or empty")

    try:
        image = normalize_image(content, allowed_formats=frozenset({"JPEG", "PNG"}))
    except InvalidImage as exc:
        raise HTTPException(400, str(exc)) from exc
    content = image.data
    fname = f"tpl_{tpl_id}_{uuid.uuid4().hex[:8]}.{image.extension}"
    fpath = os.path.join(settings.PDF_BG_DIR, fname)
    with open(fpath, "wb") as f:
        f.write(content)

    # Delete old bg (audit H-1: resolve through the traversal-safe helper so a
    # tampered background_image value can never delete a file outside PDF_BG_DIR).
    if obj.background_image:
        old_path = _safe_bg_path(obj.background_image)
        if old_path and os.path.exists(old_path):
            try:
                os.remove(old_path)
            except OSError as exc:
                # Non-fatal -- orphaned file on disk is preferable to a 500.
                # Log so ops can clean up.
                import logging
                logging.getLogger("dtc.documents").warning(
                    "Could not delete stale PDF background %r: %s", old_path, exc
                )

    obj.background_image = fname

    # ------------------------------------------------------------------
    # ASPECT-LOCK (root-cause fix for coordinate drift)
    # Both the designer (<img object-fit:fill>) and the print/PDF renderer
    # (.doc-bg background-size:100% 100%) STRETCH the scan to fill the page
    # box. That is only distortion-free when the page aspect ratio matches
    # the image aspect ratio. So on every upload we snap page_height to the
    # image's true aspect (keeping page_width), guaranteeing designer == print
    # == PDF with zero distortion and no field misalignment.
    # ------------------------------------------------------------------
    try:
        _iw, _ih = image.width, image.height
        if _iw and _ih:
            _pw = float(obj.page_width or 595) or 595.0
            _new_h = round(_pw / (_iw / _ih), 1)
            if _new_h > 0 and abs(_new_h - float(obj.page_height or 0)) >= 0.5:
                obj.page_height = _new_h
    except Exception as _asp_exc:  # never fail the upload over aspect calc
        import logging
        logging.getLogger("dtc.documents").warning(
            "Could not aspect-lock template %s to background: %s", tpl_id, _asp_exc
        )

    db.commit()
    return {
        "ok": True,
        "background_image": fname,
        "url": f"/static/pdf_backgrounds/{fname}",
        "page_width": float(obj.page_width or 595),
        "page_height": float(obj.page_height or 842),
    }


# ===== Fields =====
@router.get("/templates/{tpl_id}/fields", response_model=List[DocumentFieldOut])
def list_fields(tpl_id: int, db: Session = Depends(get_tenant_db), user: User = Depends(require_permission("documents:view"))):
    return db.query(DocumentField).filter(DocumentField.template_id == tpl_id).order_by(DocumentField.id).all()


@router.post("/templates/{tpl_id}/fields", response_model=DocumentFieldOut)
def create_field(tpl_id: int, payload: DocumentFieldCreate, db: Session = Depends(get_tenant_db), user: User = Depends(require_permission("documents:edit"))):
    tpl = db.query(DocumentTemplate).filter(DocumentTemplate.id == tpl_id).first()
    if not tpl:
        raise HTTPException(404, "Template not found")
    data = payload.model_dump()
    # Same invariant as the bulk endpoint -- this single-field path is used by
    # scripts and older clients, so it must not be a back door for impossible
    # cell geometry.
    _note = _sanitize_char_cell_meta(data)
    if _note:
        logging.getLogger("dtc.documents").warning(
            "template %s: refitted char_cells on create -- %s", tpl_id, _note)
    obj = DocumentField(template_id=tpl_id, **data)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.put("/fields/{field_id}", response_model=DocumentFieldOut)
def update_field(field_id: int, payload: DocumentFieldCreate, db: Session = Depends(get_tenant_db), user: User = Depends(require_permission("documents:edit"))):
    obj = db.query(DocumentField).filter(DocumentField.id == field_id).first()
    if not obj:
        raise HTTPException(404, "Field not found")
    data = payload.model_dump()
    _note = _sanitize_char_cell_meta(data)
    if _note:
        logging.getLogger("dtc.documents").warning(
            "field %s: refitted char_cells on update -- %s", field_id, _note)
    for k, v in data.items():
        setattr(obj, k, v)
    db.commit()
    db.refresh(obj)
    return obj


@router.delete("/fields/{field_id}")
def delete_field(field_id: int, db: Session = Depends(get_tenant_db), user: User = Depends(require_permission("documents:edit"))):
    obj = db.query(DocumentField).filter(DocumentField.id == field_id).first()
    if not obj:
        raise HTTPException(404, "Field not found")
    db.delete(obj)
    db.commit()
    return {"ok": True}


@router.get("/available-fields")
def available_fields(data_source: str = "candidate", user: User = Depends(require_permission("documents:view"))):
    """Flat list of available fields for the given data_source.
    Kept for backwards compatibility -- the designer should use
    /designer/palette for the grouped + searchable structure.
    """
    return get_available_fields(data_source)


# ============================================================================
# TEMPLATE DESIGNER -- UI helper endpoints
# ============================================================================
# These endpoints power the drag-and-drop WYSIWYG designer at
# /documents/customize/{tpl_id}. They expose the same data the manual
# /templates/{id}/fields endpoints handle, but in a shape that maps 1-to-1
# to the designer's left palette, canvas, and properties panel.

@router.get("/designer/palette")
def designer_palette(
    data_source: str = "candidate",
    user: User = Depends(require_permission("documents:view")),
):
    """Return the full field palette for the Template Designer, grouped
    exactly the way demo.oep.com.pk shows it: Customer / Category /
    Candidate / Passport / Selected Trade / Visa File / Depositor /
    System. Each group entry includes the field's default render type so
    the designer can pre-seed the right input (text vs char_cells vs
    arabic vs photo etc.).
    """
    # Field-key → default field_type. Anything not listed defaults to "text".
    DEFAULT_TYPES = {
        "photo": "photo",
        "candidate_photo": "photo",
        "name_arabic": "arabic",
        "father_name_arabic": "arabic",
        "sponsor_name_arabic": "arabic",
        "sponsor_address_arabic": "arabic",
        "issuing_authority_arabic": "arabic",
        "company_name_arabic": "arabic",
        "place_of_birth_arabic": "arabic",
        "address_arabic": "arabic",
        "cnic": "char_cells",
        "nadra_token_no": "char_cells",
        "passport_no": "char_cells",
        "next_of_kin_nic": "char_cells",
        "phone": "char_cells",
        "mobile": "char_cells",
        "e_number": "char_cells",
        "permission_no": "char_cells",
        "date_of_birth": "char_cells",
        "passport_issue_date": "char_cells",
        "passport_expiry_date": "char_cells",
        "permission_date": "char_cells",
        "visa_issue_date": "char_cells",
        "gender_male": "checkbox",
        "gender_female": "checkbox",
        "marital_single": "checkbox",
        "marital_married": "checkbox",
        "religion_islam": "checkbox",
        "religion_christian": "checkbox",
        "ticket_included": "checkbox",
        "accommodation": "checkbox",
        "food_allowance": "checkbox",
    }
    # Field icons (Font Awesome) per category for nicer palette
    GROUP_ORDER = [
        "Customer", "Category", "Candidate", "Passport",
        "Selected Trade", "Visa File", "Depositor", "Profession",
        "Contact", "Identification", "Next of Kin", "Workflow",
        "Media", "Sponsor", "Visa", "Demand File", "Client",
        "System",
    ]

    # ------------------------------------------------------------------
    # Build the base palette from pdf_engine.get_available_fields, then
    # enrich it with the screenshot-style groups that aren't present in
    # the flat schema (Customer = client/OEP, Category = document
    # category, Selected Trade = job-category rows, Depositor = depositor
    # info). For data_source=candidate we always also include the
    # surrounding "Visa File / Sponsor" group so the designer doesn't
    # have to switch sources to drop a sponsor field onto a candidate
    # document.
    # ------------------------------------------------------------------
    items = []
    seen = set()

    def _add(group, key, label, ftype=None, arabic=False):
        if key in seen:
            return
        seen.add(key)
        items.append({
            "group": group,
            "key": key,
            "label": label,
            "field_type": ftype or DEFAULT_TYPES.get(key, "arabic" if arabic else "text"),
            "arabic": arabic,
        })

    # --- Customer group (the OEP itself / the tenant company) ---
    for k, l in [
        ("customer_name", "Name"),
        ("customer_name_arabic", "Name (Arabic)"),
        ("customer_owner_name", "Owner Name"),
        ("customer_oep_license", "OEP License"),
        ("customer_emigrant_office", "Emigrant Office"),
        ("customer_office_name", "Office Name"),
        ("customer_office_city", "Office City"),
        ("customer_address", "Address"),
        ("customer_address_arabic", "Address (Arabic)"),
        ("customer_phone", "Phone"),
        ("customer_mobile", "Mobile"),
        ("customer_fax", "Fax"),
        ("customer_email", "Email"),
        ("customer_website", "Website"),
        ("customer_subdomain", "Subdomain"),
        ("customer_slug", "Slug"),
        ("customer_file_prefix", "File Prefix"),
        ("customer_starting_point", "Starting Point"),
        ("customer_status", "Status"),
        ("customer_plan", "Plan"),
        ("customer_expiry_date", "Expiry Date"),
    ]:
        _add("Customer", k, l, arabic=k.endswith("_arabic"))

    # --- Category group ---
    _add("Category", "document_category", "Document Category")

    # --- Candidate group ---
    candidate_fields = [
        ("photo",                "Photo", "photo"),
        ("full_name",            "Name", None),
        ("name_arabic",          "Name (Arabic)", "arabic"),
        ("father_name",          "Father Name", None),
        ("father_name_arabic",   "Father Name (Arabic)", "arabic"),
        ("mother_name",          "Mother Name", None),
        ("cnic",                 "CNIC", "char_cells"),
        ("nadra_token_no",       "NADRA Token", "char_cells"),
        ("date_of_birth",        "Date of Birth", None),
        ("nationality",          "Nationality", None),
        ("address",              "Address", None),
        ("phone",                "Phone", None),
        ("gender",               "Gender", None),
        ("gender_male",          "Gender -- Male (checkbox)", "checkbox"),
        ("gender_female",        "Gender -- Female (checkbox)", "checkbox"),
        ("marital_status",       "Marital Status", None),
        ("marital_single",       "Marital -- Single (checkbox)", "checkbox"),
        ("marital_married",      "Marital -- Married (checkbox)", "checkbox"),
        ("religion",             "Religion", None),
        ("religion_islam",       "Religion -- Islam (checkbox)", "checkbox"),
        ("religion_christian",   "Religion -- Christian (checkbox)", "checkbox"),
        ("place_of_birth",       "Place of Birth", None),
        ("place_of_birth_arabic","Place of Birth (Arabic)", "arabic"),
        ("tehsil",               "Tehsil", None),
        ("district",             "District", None),
        ("province",             "Province", None),
        ("qualification",        "Qualification", None),
        ("profession",           "Occupation / Profession", None),
        ("profession_arabic",    "Occupation (Arabic)", "arabic"),
        ("age_employee",         "Age / Employee", None),
        ("permission_no",        "Permission No", None),
        ("permission_date",      "Permission Date", None),
        ("salary",               "Salary", None),
        ("price",                "Price", None),
        ("ticket_included",      "Ticket Included", "checkbox"),
        ("accommodation_allowance", "Accommodation Allowance", None),
        ("food_allowance",       "Food Allowance", None),
        ("slot_notes",           "Slot Notes", None),
        ("status",               "Status", None),
        ("protector_no",         "Protector No", None),
        ("protector_date",       "Protector Date", None),
        ("next_of_kin_name",     "Next of Kin Name", None),
        ("next_of_kin_nic",      "Next of Kin CNIC", "char_cells"),
        ("next_of_kin_relation", "Next of Kin Relation", None),
        ("medical_center",       "Medical Center", None),
        ("gamca_number",         "GAMCA Number", None),
        ("medical_date",         "Medical Date", None),
        ("e_number",             "E-Number", None),
        ("date_of_departure",    "Departure Date", None),
        ("flight_no",            "Flight Number", None),
        ("destination",          "Destination", None),
        ("ticket_no",            "Ticket Number", None),
        ("visa_stamp_date",      "Visa Stamp Date", None),
    ]
    for k, l, t in candidate_fields:
        _add("Candidate", k, l, ftype=t, arabic=(t == "arabic"))

    # --- Passport group ---
    for k, l, t in [
        ("passport_no",          "Number", "char_cells"),
        ("passport_issue_date",  "Issue Date", None),
        ("passport_expiry_date", "Expiry Date", None),
        ("issuing_authority",    "Authority", None),
        ("issuing_authority_arabic", "Authority (Arabic)", "arabic"),
        ("passport_issue_place", "Issue Place", None),
        ("passport_receive_date", "Receive Date", None),
        ("passport_send_date",   "Send Date", None),
        # Canonical keys. These used to be advertised as `passport_consignment`
        # / `passport_courier`, which the resolver never produced -- so anything
        # dropped from them printed blank. resolve_overlay_fields() keeps the old
        # spellings working for templates already saved against them.
        ("passport_consignment_no", "Consignment No", None),
        ("passport_courier_name",   "Courier Name", None),
        ("passport_receive_date_2", "Receive Date 2", None),
    ]:
        _add("Passport", k, l, ftype=t, arabic=(t == "arabic"))

    # --- Selected Trade group (job category line items) ---
    # Canonical `selected_trade_*` keys. The palette previously offered these as
    # `trade_sr_no` / `trade_qty` / `trade_assigned` / `trade_available` /
    # `trade_salary` / `trade_contract_period`, none of which the resolver ever
    # emitted -- six palette entries that silently printed nothing. The old
    # spellings still resolve (compatibility aliases in resolve_overlay_fields)
    # so templates saved earlier now start working instead of staying blank.
    for k, l in [
        ("selected_trade_sr",            "Sr#"),
        ("selected_trade_visa_category", "Visa Category (Selected)"),
        ("selected_trade_visa_category_arabic", "Visa Category (Arabic)"),
        ("trade_visa_category",          "Visa Category"),
        ("selected_trade_qty",           "Quantity"),
        ("selected_trade_assigned",      "Assigned"),
        ("selected_trade_available",     "Available"),
        ("salary",                       "Salary"),
        ("contract_years",               "Contract Years"),
        ("contract_period",              "Contract Period"),
    ]:
        _add("Selected Trade", k, l)
    _add("Selected Trade", "trades_table", "Trades Table", ftype="trade_table")

    # --- Visa File / Demand group ---
    for k, l, t in [
        ("file_number",            "File Number", None),
        # `company_name` is what the resolver produces; `client_name` was a
        # palette-only name that never resolved.
        ("company_name",           "Client Name", None),
        ("sponsor_name",           "Sponsor Name", None),
        ("sponsor_name_arabic",    "Sponsor Name (Arabic)", "arabic"),
        ("sponsor_address",        "Sponsor Address", None),
        ("sponsor_address_arabic", "Sponsor Address (Arabic)", "arabic"),
        ("sponsor_phone",          "Sponsor Phone", None),
        ("visa_number",            "Visa Number", None),
        ("permission_no",          "Permission Number", None),
        ("visa_issue_date",        "Visa Issue Date", None),
        ("visa_issue_date_hijri",  "Visa Issue Date (Hijri)", None),
        # AUDIT 2026-07-30 -- operator report: "sponsor id / bataqa no. data
        # field missing in documents coordinate engine". Both were live columns
        # (Client.sponsor_id, Demand.bataka_number) holding real data that the
        # palette simply never offered, so they could not be dragged onto the
        # Saudi visa form at all -- the operator had to fake them with a static
        # text box, which then printed the literal placeholder.
        ("sponsor_id",             "Sponsor ID / Bataqa No", None),
        ("bataka_number",          "Bataka (Bataqa) Number", None),
        ("sponsor_alt_phone",      "Sponsor Alt Phone", None),
        ("demand_code",            "Demand Code", None),
        ("visa_quota",             "Visa Quota", None),
        ("benefits",               "Benefits", None),
        ("expiry_date",            "Visa File Expiry Date", None),
        ("receiving_date",         "Receiving Date", None),
        ("country",                "Country", None),
        ("embassy",                "Embassy", None),
        ("embassy_city",           "Embassy City", None),
        ("reference",              "Reference", None),
        ("notes",                  "Notes", None),
    ]:
        _add("Visa File", k, l, ftype=t, arabic=(t == "arabic"))

    # --- Depositor group ---
    for k, l in [
        ("depositor_first_name", "First Name"),
        ("depositor_last_name",  "Last Name"),
        ("depositor_full_name",  "Full Name"),
        ("depositor_mobile",     "Mobile Number"),
        ("depositor_cnic",       "CNIC"),
    ]:
        _add("Depositor", k, l, ftype="char_cells" if k.endswith("cnic") or k.endswith("mobile") else None)

    # --- System group (always available) ---
    _add("System", "__today__", "Today's Date")
    _add("System", "__now__",   "Current Date + Time")
    _add("System", "page_number", "Page Number")

    # --- Other / Insert primitives (these don't bind to a data column;
    # they let the user place free-form widgets that read from
    # field.static_value or always render as a static checkbox/barcode).
    # The designer shows these under an "Insert" toolbar, but we also
    # surface them in the palette for parity with demo.oep.
    for k, l, t in [
        ("__static_text__",     "Insert Text Box",  "static"),
        ("__static_checkbox__", "Insert Checkbox",  "checkbox"),
        ("__static_barcode__",  "Insert Barcode",   "barcode"),
    ]:
        _add("Other", k, l, ftype=t)

    # ------------------------------------------------------------------
    # COMPLETENESS SWEEP
    # ------------------------------------------------------------------
    # AUDIT 2026-07-30 -- operator report: "why missing data fields, I ask each
    # and every data field in documents coordinate engine for drag and drop".
    #
    # Everything above is a curated list: it controls grouping, labels and
    # render types for the fields that matter most on the shipped forms. But a
    # curated list is exactly what caused the reported bug -- columns nobody
    # remembered to add (Demand.bataka_number, Client.sponsor_id) could not be
    # dragged onto a form at all, and the operator had to fake them with a
    # static text box.
    #
    # So after the curated groups, sweep in EVERY remaining field the resolver
    # can produce, derived from the ORM models via the same alias rules the
    # resolver itself applies. _add() skips keys already seen, so curated
    # labels/types/groups always win and this only ever ADDS. Adding a column to
    # Candidate/Demand/JobCategory/Client now makes it draggable automatically.
    from app.services.overlay_engine import resolvable_short_keys

    for _key, _group in sorted(resolvable_short_keys().items()):
        _label = _key.replace("_", " ").title()
        # Tidy the abbreviations that Title() mangles, so the palette reads
        # like the rest of the UI rather than like column names.
        for _bad, _good in (
            ("Cnic", "CNIC"), ("Nic", "NIC"), ("No ", "No. "), ("Oep", "OEP"),
            ("Id ", "ID "), ("Url", "URL"), ("Sr ", "Sr# "), ("Dob", "DOB"),
        ):
            _label = _label.replace(_bad, _good)
        if _label.endswith(" Id"):
            _label = _label[:-3] + " ID"
        if _label.endswith(" No"):
            _label = _label[:-3] + " No."
        _add(_group, _key, _label)

    # Sort items so that GROUP_ORDER is honoured but unknown groups land
    # at the end (alphabetically).
    def _grp_rank(g):
        return (GROUP_ORDER.index(g), 0) if g in GROUP_ORDER else (len(GROUP_ORDER), g)
    items.sort(key=lambda it: (_grp_rank(it["group"]), it["label"].lower()))

    # Build the grouped output shape the designer consumes directly.
    grouped = {}
    for it in items:
        grouped.setdefault(it["group"], []).append({
            "key": it["key"],
            "label": it["label"],
            "field_type": it["field_type"],
            "arabic": it["arabic"],
        })
    ordered_groups = [
        {"group": g, "fields": grouped[g]}
        for g in sorted(grouped.keys(), key=_grp_rank)
    ]

    return {
        "data_source": data_source,
        "groups": ordered_groups,
        # Flat list still available for clients that want it.
        "flat": items,
    }


@router.get("/designer/backgrounds")
def designer_backgrounds(user: User = Depends(require_permission("documents:view"))):
    """List every background image available under /static/pdf_backgrounds/
    so the designer can swap a template's background without leaving the
    page. Returns the bare filenames in sorted order.
    """
    bg_dir = settings.PDF_BG_DIR
    out = []
    try:
        for name in sorted(os.listdir(bg_dir)):
            if name.lower().endswith((".jpg", ".jpeg", ".png")):
                full = os.path.join(bg_dir, name)
                size = os.path.getsize(full) if os.path.isfile(full) else 0
                iw = ih = None
                try:
                    from PIL import Image
                    with Image.open(full) as _im:
                        iw, ih = _im.size
                except Exception:
                    iw = ih = None
                out.append({
                    "filename": name,
                    "url": f"/static/pdf_backgrounds/{name}",
                    "size": size,
                    "width": iw,
                    "height": ih,
                })
    except FileNotFoundError:
        pass
    return out


@router.post("/templates/{tpl_id}/fields/bulk")
def bulk_save_fields(
    tpl_id: int,
    payload: dict,
    db: Session = Depends(get_tenant_db),
    user: User = Depends(require_permission("documents:edit")),
):
    """Replace the entire field set for a template in one shot. This is
    what the designer's "Save" button calls -- it sends the full canvas
    state and we sync the document_fields rows to match.

    Body shape::

        {
          "fields": [
            { "id": 12, "label": "Name", "field_key": "full_name",
              "field_type": "text", "x": 100, "y": 700, "width": 200,
              "height": 18, "font_size": 12, "font_bold": true,
              "font_italic": false, "color": "#000", "align": "left",
              "page": 1, "static_value": "", "meta": {} },
            ...   # rows WITHOUT id are inserted; rows WITH id are updated
          ],
          "deleted_ids": [4, 5]   # rows to delete (optional)
        }

    The endpoint is idempotent and atomic -- if any row fails validation
    nothing is persisted.
    """
    tpl = db.query(DocumentTemplate).filter(DocumentTemplate.id == tpl_id).first()
    if not tpl:
        raise HTTPException(404, "Template not found")

    incoming = payload.get("fields") or []
    deleted_ids = payload.get("deleted_ids") or []

    if not isinstance(incoming, list):
        raise HTTPException(400, "`fields` must be a list")

    # Load existing field rows keyed by id for fast lookup.
    existing = {f.id: f for f in db.query(DocumentField).filter(DocumentField.template_id == tpl_id).all()}

    # 1) Apply explicit deletions first.
    for fid in deleted_ids:
        try:
            fid = int(fid)
        except (TypeError, ValueError):
            continue
        if fid in existing:
            db.delete(existing.pop(fid))

    # 2) Upsert each incoming field.
    #
    # CRITICAL BUG FIX (2026-06-01):  earlier versions trusted the
    # client to always send `id` for existing rows. If the client lost
    # the id (e.g. after an error, or before the post-save reconcile
    # finishes) every Save would create a fresh copy → over time a
    # single template accumulated thousands of duplicate rows.
    #
    # The new logic is "REPLACE ALL": we apply the incoming list as
    # the FULL canvas state. Any existing row whose id is NOT in the
    # incoming list is deleted at the end. We also dedupe by
    # (field_key, page, round(x), round(y)) inside the request so the
    # client cannot accidentally insert two identical fields.
    out_ids: list[int] = []
    kept_ids: set[int] = set()
    pos_seen: set[tuple] = set()
    geometry_notes: list[str] = []

    for raw in incoming:
        if not isinstance(raw, dict):
            continue

        # Coerce + validate the bits we care about.
        try:
            row = {
                "label":       str(raw.get("label") or raw.get("field_key") or "Field"),
                "field_key":   str(raw.get("field_key") or "").strip(),
                "field_type":  str(raw.get("field_type") or "text"),
                "static_value": raw.get("static_value") or "",
                "x":           float(raw.get("x") or 0),
                "y":           float(raw.get("y") or 0),
                "width":       float(raw.get("width") or 200),
                "height":      float(raw.get("height") or 20),
                "font_size":   float(raw.get("font_size") or 11),
                "font_bold":   bool(raw.get("font_bold")),
                "font_italic": bool(raw.get("font_italic")),
                "color":       str(raw.get("color") or "#000000"),
                "align":       str(raw.get("align") or "left"),
                "page":        int(raw.get("page") or 1),
                "meta":        raw.get("meta") or {},
            }
        except (TypeError, ValueError) as exc:
            db.rollback()
            raise HTTPException(400, f"Invalid field payload: {exc}") from exc

        if not row["field_key"]:
            # Skip empty key rows silently -- the designer occasionally emits
            # placeholder rows for newly-dragged elements that the user
            # cancelled before naming.
            continue

        # Never persist a character grid that cannot fit inside its own box.
        # See _sanitize_char_cell_meta: resizing a field does not rescale
        # meta.cell_width, so an ordinary drag could silently corrupt the
        # geometry the whole coordinate engine depends on.
        _note = _sanitize_char_cell_meta(row)
        if _note:
            geometry_notes.append(_note)

        # Per-request dedupe -- drop exact-position duplicates.
        pos_key = (row["field_key"], row["page"], round(row["x"]), round(row["y"]))
        if pos_key in pos_seen:
            continue
        pos_seen.add(pos_key)

        fid = raw.get("id")
        try:
            fid_int = int(fid) if fid not in (None, "", 0, "0") else None
        except (TypeError, ValueError):
            fid_int = None

        obj = existing.get(fid_int) if fid_int else None

        # Second-chance match: if the client lost the id, try to match an
        # existing row by (field_key, page, ~x, ~y). Coordinates are
        # rounded to the nearest point because canvas drag-snap can
        # produce sub-pixel jitter (e.g. 122.0001 vs 122.0).
        if obj is None:
            for ex_id, ex_obj in existing.items():
                if ex_id in kept_ids:
                    continue
                if (ex_obj.field_key == row["field_key"]
                        and ex_obj.page == row["page"]
                        and round(ex_obj.x) == round(row["x"])
                        and round(ex_obj.y) == round(row["y"])):
                    obj = ex_obj
                    break

        if obj is None:
            obj = DocumentField(template_id=tpl_id, **row)
            db.add(obj)
        else:
            for k, v in row.items():
                setattr(obj, k, v)
        db.flush()
        out_ids.append(obj.id)
        kept_ids.add(obj.id)

    # 3) REPLACE-ALL semantics -- any existing row NOT touched above is
    # dropped. This is what guarantees the canvas state == the DB state
    # and eliminates the duplicate-on-save bug.
    for ex_id, ex_obj in list(existing.items()):
        if ex_id not in kept_ids:
            db.delete(ex_obj)

    db.commit()
    # Re-fetch ordered list for the client.
    fields = (
        db.query(DocumentField)
        .filter(DocumentField.template_id == tpl_id)
        .order_by(DocumentField.id)
        .all()
    )
    if geometry_notes:
        logging.getLogger("dtc.documents").warning(
            "template %s: refitted %d char_cells field(s) on save -- %s",
            tpl_id, len(geometry_notes), "; ".join(geometry_notes),
        )

    return {
        "ok": True,
        "saved_ids": out_ids,
        "deleted_ids": deleted_ids,
        "total_fields": len(fields),
        # Surfaced so the designer can tell the operator what was corrected,
        # rather than quietly changing a number they typed.
        "geometry_notes": geometry_notes,
        "fields": [
            {
                "id": f.id, "label": f.label, "field_key": f.field_key,
                "field_type": f.field_type, "static_value": f.static_value or "",
                "x": f.x, "y": f.y, "width": f.width, "height": f.height,
                "font_size": f.font_size, "font_bold": f.font_bold,
                "font_italic": f.font_italic, "color": f.color, "align": f.align,
                "page": f.page, "meta": f.meta or {},
            }
            for f in fields
        ],
    }


@router.post("/templates/{tpl_id}/fields/dedupe")
def dedupe_fields(
    tpl_id: int,
    db: Session = Depends(get_tenant_db),
    user: User = Depends(require_permission("documents:designer")),
):
    """One-shot cleanup: collapse duplicate fields on a template.

    A field is considered a duplicate of another when both share the
    same (field_key, page, round(x), round(y)). The OLDEST row (lowest
    id) wins so any record bound to that id keeps working; all newer
    copies are deleted.
    """
    tpl = db.query(DocumentTemplate).filter(DocumentTemplate.id == tpl_id).first()
    if not tpl:
        raise HTTPException(404, "Template not found")

    rows = (
        db.query(DocumentField)
        .filter(DocumentField.template_id == tpl_id)
        .order_by(DocumentField.id.asc())
        .all()
    )
    before = len(rows)
    seen: dict[tuple, int] = {}
    deleted = 0
    for f in rows:
        key = (f.field_key or "", int(f.page or 1), round(f.x or 0), round(f.y or 0))
        if key in seen:
            db.delete(f)
            deleted += 1
        else:
            seen[key] = f.id
    db.commit()
    return {"ok": True, "before": before, "after": before - deleted, "deleted": deleted}


@router.post("/admin/dedupe-all-fields")
def dedupe_all_fields(
    db: Session = Depends(get_tenant_db),
    user: User = Depends(require_permission("documents:designer")),
):
    """Admin housekeeping -- run the dedupe pass across EVERY template.

    Returns a per-template summary. Safe to invoke multiple times: it
    is idempotent (the second call always reports 0 deletions because
    no duplicates remain).
    """
    summary = []
    tpls = db.query(DocumentTemplate.id, DocumentTemplate.name).all()
    for tpl in tpls:
        rows = (
            db.query(DocumentField)
            .filter(DocumentField.template_id == tpl.id)
            .order_by(DocumentField.id.asc())
            .all()
        )
        before = len(rows)
        seen: dict[tuple, int] = {}
        deleted = 0
        for f in rows:
            key = (f.field_key or "", int(f.page or 1),
                   round(f.x or 0), round(f.y or 0))
            if key in seen:
                db.delete(f)
                deleted += 1
            else:
                seen[key] = f.id
        if deleted:
            summary.append({
                "template_id": tpl.id, "name": tpl.name,
                "before": before, "after": before - deleted,
                "deleted": deleted,
            })
    db.commit()
    return {"ok": True, "templates_touched": len(summary), "details": summary}


def _parse_record_id(record_id):
    """Tolerate empty-string ?record_id= (the frontend may pass it blank when
    no record is selected). Returns int or None."""
    if record_id is None or record_id == "" or str(record_id).lower() in ("null", "none", "undefined"):
        return None
    try:
        return int(record_id)
    except (TypeError, ValueError):
        return None


# ===== Generate PDF =====
@router.get("/templates/{tpl_id}/generate")
def generate(tpl_id: int, request: Request, record_id: Optional[str] = Query(None),
             db: Session = Depends(get_tenant_db),
             user: User = Depends(require_permission("documents:print"))):
    record_id = _parse_record_id(record_id)
    tpl = db.query(DocumentTemplate).options(joinedload(DocumentTemplate.fields)).filter(DocumentTemplate.id == tpl_id).first()
    if not tpl:
        raise HTTPException(404, "Template not found")
    try:
        path = generate_pdf(db, tpl, record_id=record_id)
    except Exception as e:
        raise HTTPException(500, f"PDF generation failed: {e}") from e

    # Log (named `gen_log`, not `log` -- see the note in print_document(): a
    # local called `log` shadows the module logger for the whole function).
    gen_log = GeneratedDocument(template_id=tpl_id, record_id=record_id, file_path=path, generated_by=user.id)
    db.add(gen_log)
    db.commit()

    # AUDIT (2026-08-15): a generated PDF discloses the candidate's passport
    # number, CNIC and address. GeneratedDocument already recorded that a file
    # was produced, but it is a document-management row, not an access trail:
    # it is not queried by the audit viewer and carries no IP/user-agent. The
    # audit row is what answers "who downloaded this, for whom, from where?".
    log_document_action(
        db, action=AuditAction.PRINT.value, template=tpl, actor=user,
        request=request, record_id=record_id, detail="pdf download",
    )

    return FileResponse(path, media_type="application/pdf", filename=os.path.basename(path))


# ============================================================================
# REAL-TIME DATA OVERLAY -- Live preview JSON
# ============================================================================
@router.get("/templates/{tpl_id}/preview-data")
def preview_data(
    tpl_id: int,
    request: Request,
    record_id: Optional[str] = Query(None),
    db: Session = Depends(get_tenant_db),
    user: User = Depends(require_permission("documents:view")),
):
    record_id = _parse_record_id(record_id)
    """Return the merged field+value payload for the document customize page
    so the frontend can render a live overlay on the background image.
    """
    tpl = (
        db.query(DocumentTemplate)
        .options(joinedload(DocumentTemplate.fields))
        .filter(DocumentTemplate.id == tpl_id)
        .first()
    )
    if not tpl:
        raise HTTPException(404, "Template not found")
    record = _load_record(db, tpl.data_source, record_id)
    payload = _build_overlay_payload(tpl, record, db=db)
    payload["record_id"] = record_id
    payload["record_loaded"] = record is not None
    # AUDIT (2026-08-15): unlike get_template (which returns only the field
    # geometry), this endpoint merges the REAL record values into the payload
    # -- it is a disclosure of the candidate's data, so it is recorded with
    # the record id, not just the template id.
    log_document_action(
        db, action=AuditAction.VIEW.value, template=tpl, actor=user,
        request=request, record_id=record_id, detail="overlay preview data",
    )
    return payload


# ============================================================================
# IN-BROWSER PRINT VIEW -- renders HTML page with image + data overlay and
# auto-triggers the browser's native print dialog. NOT a PDF download.
# ============================================================================
# ---------------------------------------------------------------------------
# Cached background inliner
# ---------------------------------------------------------------------------
# Print endpoints inline the background image (letterhead / form scan) as a
# base64 data: URI so the print job is self-contained on mobile browsers that
# would otherwise drop network-loaded backgrounds. But re-reading & re-encoding
# a 500 KB JPEG on every print request cost ~200-400ms of wall time PLUS an
# extra 700+ KB of ASCII text in each response — this stacked with the auto-
# print image-load wait on the client to produce the "4-5 minute wait then
# broken document" report from field. Cache the encoded result: static
# backgrounds shipped in the app image never change, so a small LRU is safe.
@lru_cache(maxsize=64)
def _cached_static_data_uri(fs_path: str) -> Optional[str]:
    """Read + base64-encode a static file exactly ONCE per process lifetime.

    Returns ``None`` on any read error so the caller can fall back to the
    plain URL path (browser will fetch it directly).
    """
    try:
        with open(fs_path, "rb") as _bf:
            _raw = _bf.read()
        _mime = _mt_top.guess_type(fs_path)[0] or "image/jpeg"
        return f"data:{_mime};base64," + _b64_top.b64encode(_raw).decode("ascii")
    except OSError as _be:
        log.warning("could not inline background %s: %s", fs_path, _be)
        return None


@router.get("/templates/{tpl_id}/print", response_class=HTMLResponse)
def print_document(
    tpl_id: int,
    request: Request,
    record_id: Optional[str] = Query(None),
    auto: int = Query(1, description="Auto-trigger window.print() after image loads"),
    fit: int = Query(0, description="Scale A4 page to fit viewport width (for in-app modal preview)"),
    db: Session = Depends(get_tenant_db),
    user: User = Depends(require_permission("documents:print")),
):
    record_id = _parse_record_id(record_id)
    """Render an HTML page that overlays the candidate/demand data on top of
    the document's background image at the exact field coordinates, and
    immediately invokes the browser's print dialog. This is the user-visible
    'Print' behaviour from the Documents tab -- NO file is downloaded, no new
    tab is opened with a blank PDF; instead the standard browser print panel
    appears with the live data already overlaid.
    """
    tpl = (
        db.query(DocumentTemplate)
        .options(joinedload(DocumentTemplate.fields))
        .filter(DocumentTemplate.id == tpl_id)
        .first()
    )
    if not tpl:
        raise HTTPException(404, "Template not found")

    # AUDIT (2026-08-15): this is the operator-facing Print action -- it
    # renders the candidate's live passport/CNIC/address onto the form and
    # opens the browser print dialog. No file is downloaded, so the
    # GeneratedDocument table never sees it: before this, the most common way
    # a document was actually printed left NO trace anywhere in the system.
    log_document_action(
        db, action=AuditAction.PRINT.value, template=tpl, actor=user,
        request=request, record_id=record_id, detail="html print view",
    )

    record = _load_record(db, tpl.data_source, record_id)
    payload = _build_overlay_payload(tpl, record, db=db)

    # Parse the designer meta-fence from the description so Content-tab notes
    # actually render on the printed page (audit Fix 3 -- previously the notes
    # were saved via /templates PUT but never appeared anywhere on output).
    from app.services.overlay_engine import parse_designer_meta
    designer_meta = parse_designer_meta(tpl.description or "")
    content_notes = (designer_meta.get("notes") or "").strip()

    # Log the print action.
    # NAMING (audit 2026-08-11): this row used to be called `log`, which
    # shadowed the module-level `log = logging.getLogger(...)` for the rest of
    # the function -- so any log call added later in print_document() died with
    # "'GeneratedDocument' object has no attribute 'warning'". Renamed so the
    # logger stays reachable.
    print_log = GeneratedDocument(
        template_id=tpl_id, record_id=record_id,
        file_path=f"print:{tpl.name}", generated_by=user.id,
    )
    db.add(print_log); db.commit()

    page_w = float(tpl.page_width or 595)   # PDF points (1pt = 1/72 inch)
    page_h = float(tpl.page_height or 842)
    # Convert PDF points to mm for CSS (1pt = 0.3528 mm)
    PT_TO_MM = 25.4 / 72.0
    page_w_mm = page_w * PT_TO_MM
    page_h_mm = page_h * PT_TO_MM

    # --- CRITICAL PRINT FIX: use a NAMED @page size when the template matches a
    # standard paper size -------------------------------------------------------
    # A custom `@page { size: 209.90mm 296.93mm }` (595x842pt rounded to mm) does
    # NOT exactly equal ISO A4 (210x297mm). Real physical printer drivers treat a
    # non-matching custom size as an unloaded "custom paper" tray: the spooler
    # accepts the job and reports SUCCESS, but the driver silently discards the
    # physical output -- which is EXACTLY the reported symptom (job succeeds, but
    # portal docs never physically print, while receipts -- which already use
    # `@page { size: A4 portrait }` -- print fine).
    #
    # So: detect the standard size within a small tolerance and emit the NAMED
    # size keyword the driver understands (A4 / A5 / Letter / Legal), falling back
    # to precise mm only for genuinely non-standard templates. Named sizes map to
    # the loaded paper tray and print reliably on every driver.
    def _named_page_size(w_pt: float, h_pt: float) -> str:
        # (name, width_pt, height_pt) for common sizes at 72 dpi.
        STD = [
            ("A4", 595.0, 842.0),
            ("A5", 420.0, 595.0),
            ("Letter", 612.0, 792.0),
            ("Legal", 612.0, 1008.0),
            ("A3", 842.0, 1191.0),
        ]
        tol = 3.0  # pt (~1mm) tolerance for rounding differences
        for name, sw, sh in STD:
            if abs(w_pt - sw) <= tol and abs(h_pt - sh) <= tol:
                return f"{name} portrait"
            if abs(w_pt - sh) <= tol and abs(h_pt - sw) <= tol:
                return f"{name} landscape"
        # Non-standard template: keep precise mm so layout still maps 1:1.
        return f"{w_pt * PT_TO_MM:.2f}mm {h_pt * PT_TO_MM:.2f}mm"

    page_size_css = _named_page_size(page_w, page_h)

    # Group fields by page
    pages: dict = {}
    for f in payload["fields"]:
        pages.setdefault(f["page"], []).append(f)
    if not pages:
        pages[1] = []
    page_numbers = sorted(pages.keys())

    bg_url = payload["template"]["background_url"] or ""

    # --- Robust background for mobile printing -------------------------------
    # Android/Chrome print frequently drops a network-loaded <img> background
    # (timing/auth/"Background graphics" off), which is exactly why a printed
    # page can come out almost blank with only the top text + barcodes (the
    # overlay) visible. To make the background bullet-proof we inline it as a
    # base64 data: URI so it is part of the HTML document itself and cannot
    # fail to load, and below we force print-color-adjust:exact so the browser
    # is not allowed to strip it on print.
    def _inline_bg(url: str) -> str:
        """Inline a /static/ background path as a base64 data URI (bullet-proof
        for mobile print). Non-/static/ URLs are returned unchanged.

        Uses a process-wide LRU cache (see _cached_static_data_uri above) so
        the same background is only encoded once — huge win for print latency.
        """
        if not url or not url.startswith("/static/"):
            return url
        _fs_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "static",
            url[len("/static/"):],
        )
        _cached = _cached_static_data_uri(_fs_path)
        return _cached if _cached is not None else url

    bg_url = _inline_bg(bg_url)

    # --- Per-page backgrounds -------------------------------------------------
    # A single template can carry a DIFFERENT background per page via the
    # designer meta key ``page_backgrounds`` (e.g. the OEP form: front on page 1,
    # back on page 2 → ONE printable document). The value maps a page number
    # (string or int) to either a "/static/pdf_bg/<file>.jpg" path or a bare
    # filename that is resolved under the PDF background dir. Page 1 always
    # falls back to the template's own background_image.
    page_bg: dict = {}
    _pbg_meta = designer_meta.get("page_backgrounds")
    if isinstance(_pbg_meta, dict):
        for _k, _v in _pbg_meta.items():
            try:
                _pn = int(_k)
            except (TypeError, ValueError):
                continue
            _val = str(_v or "").strip()
            if not _val:
                continue
            if not _val.startswith("/static/") and not _val.startswith("http") and not _val.startswith("data:"):
                # bare filename → resolve under the static pdf background dir
                _val = f"/static/pdf_backgrounds/{_val}"
            page_bg[_pn] = _inline_bg(_val)

    def _bg_for_page(pn: int) -> str:
        return page_bg.get(pn, bg_url)

    # --- Background FIT mode (E2 coordinate-mismatch fix, 2026-07-31) --------
    # REPORTED: "field placed in the coordinate engine does not appear at the
    # same location in the print preview".
    #
    # ROOT CAUSE: the `.doc-bg` CSS class below hardcoded
    # `background-size: 100% 100%`, i.e. it ALWAYS stretched the scan to the
    # full page. But the designer canvas honours the template's `bg_fit` meta
    # key with three modes, and so does the PDF engine
    # (pdf_engine._bg_placement / _draw_background):
    #
    #     fill    -> stretch to the page box (may distort)
    #     contain -> scale down keeping aspect ratio, pinned TOP-LEFT
    #     actual  -> 1px == 1pt, pinned TOP-LEFT, unscaled
    #
    # So for any template saved as `contain` or `actual`, the designer and the
    # exported PDF drew the form artwork at one size while the HTML print
    # preview drew it stretched. Field coordinates were identical in all three
    # -- it was the ARTWORK underneath that moved -- which reads to the operator
    # as "the field is in the wrong place in the preview".
    #
    # The preview now derives its background CSS from the same `bg_fit` value,
    # so designer == print preview == PDF for every mode. `actual` needs the
    # image's intrinsic pixel size, because CSS `background-size: auto` resolves
    # px against the 96 dpi CSS reference and NOT against PDF points.
    _bg_fit = str(designer_meta.get("bg_fit") or "fill").strip().lower()
    if _bg_fit not in ("fill", "contain", "actual"):
        _bg_fit = "fill"

    def _bg_intrinsic_pt(url: str) -> Optional[tuple]:
        """Intrinsic (w, h) of a background, expressed in PDF points (1px = 1pt).

        Only needed for `actual`. Returns None when the size cannot be
        determined, in which case the caller falls back to `contain` rather
        than silently stretching.
        """
        try:
            from PIL import Image as _PILImage
            if url.startswith("data:"):
                import base64 as _b64
                _, _, b64 = url.partition(",")
                with _PILImage.open(io.BytesIO(_b64.b64decode(b64))) as im:
                    return float(im.width), float(im.height)
            if url.startswith("/static/"):
                _fs = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                    "static", url[len("/static/"):],
                )
                with _PILImage.open(_fs) as im:
                    return float(im.width), float(im.height)
        except Exception as exc:  # noqa: BLE001 -- preview must still render
            # NOTE: the module-level `log` logger is SHADOWED inside this
            # function by the `log = GeneratedDocument(...)` audit row above,
            # so `log.warning` here would raise AttributeError. Resolve the
            # logger by name instead of relying on the enclosing scope.
            logging.getLogger("dtc.documents").warning(
                "could not read background intrinsic size for %s: %s", url, exc
            )
        return None

    def _bg_style_for(url: str) -> str:
        """Inline style that reproduces `bg_fit` for this specific background."""
        if _bg_fit == "contain":
            return "background-size:contain;background-position:top left;"
        if _bg_fit == "actual":
            dims = _bg_intrinsic_pt(url)
            if dims:
                iw, ih = dims
                return (f"background-size:{iw * PT_TO_MM:.3f}mm {ih * PT_TO_MM:.3f}mm;"
                        "background-position:top left;")
            return "background-size:contain;background-position:top left;"
        return "background-size:100% 100%;background-position:top left;"

    # Build HTML
    field_html_per_page = []
    for pn in page_numbers:
        items_html = []
        for f in pages[pn]:
            # PDF coord origin = bottom-left → CSS origin = top-left.
            #
            # ===================================================================
            # UNIFIED BASELINE MODEL (definitive fix for designer↔print↔PDF drift)
            # ===================================================================
            # The single source of truth is the PDF engine, which draws text with
            # reportlab drawString() at:
            #     baseline_pdf_y = y + max(h - font_size, 0)
            # i.e. the glyph BASELINE sits that many points above the page bottom.
            #
            # Earlier versions positioned the HTML field as a *box* and let the
            # text flow from the top/bottom edge. That is unreliable because an
            # HTML line-box adds half-leading above the cap and the rendered
            # baseline sits ~0.2·font_size above the box bottom -- so the visible
            # glyphs drifted up by roughly half a character vs the PDF (exactly
            # the symptom reported: "text hovers above the line on print").
            #
            # We now position EVERY text field by its BASELINE, identical to the
            # PDF. We render the text in a single-line box of height = font_size,
            # line-height = font_size, and place that box so its baseline lands at
            # the PDF baseline. For Arial/Helvetica the baseline sits ascent≈0.80
            # ·font_size below the cap-top, so:
            #     css_top = page_h - (baseline_pdf_y + ASCENT·font_size)
            # The designer canvas uses the IDENTICAL formula, so what you see is
            # exactly what prints and exactly what the PDF exports.
            # COORDINATE CONTRACT (2026-08-18). ASCENT used to be a literal
            # 0.800 here and 0.8 in engine.ts, bound together by nothing but the
            # comment above. Nothing in the suite referenced either name, so an
            # edit to one copy would have moved printed values off their boxes on
            # live embassy and bank paperwork with no test failing. Both sides now
            # derive from app/core/geometry_contract.py, and
            # tests/test_geometry_contract.py parses engine.ts to assert the
            # TypeScript copy still matches this one.
            ASCENT = ASCENT_RATIO
            fs = float(f["font_size"] or 11)
            # Identical fallback to the PDF engine and the designer canvas.
            fld_h = effective_height(f["height"], fs)
            field_meta = f.get("meta") or {}
            padding_pt = max(0.0, float(field_meta.get("padding") or 0))
            letter_spacing_pt = float(field_meta.get("letter_spacing") or 0)
            configured_line_height = float(field_meta.get("line_height") or 0)
            opacity = min(100.0, max(0.0, float(field_meta.get("opacity", 100) or 100))) / 100.0
            multiline = bool(field_meta.get("multiline"))
            decorations = []
            if field_meta.get("font_underline"):
                decorations.append("underline")
            if field_meta.get("font_strikethrough"):
                decorations.append("line-through")
            font_name = _css_font_stack(field_meta.get("font_name"))
            baseline_pdf_y = float(f["y"]) + max(fld_h - fs, 0.0)
            # Top of the single-line text box (cap-top), in CSS-from-top points.
            text_top_pt = page_h - (baseline_pdf_y + ASCENT * fs)
            css_left_mm = f["x"] * PT_TO_MM
            css_top_mm = text_top_pt * PT_TO_MM
            css_width_mm = f["width"] * PT_TO_MM
            # The box is exactly one line tall (font_size); line-height locks the
            # baseline so no leading can shift it.
            style = (
                f"left:{css_left_mm:.2f}mm;"
                f"top:{css_top_mm:.2f}mm;"
                f"width:{css_width_mm:.2f}mm;"
                f"height:{(fld_h if multiline else fs) * PT_TO_MM:.2f}mm;"
                f"line-height:{configured_line_height if configured_line_height > 0 else fs:.2f}{'' if configured_line_height > 0 else 'pt'};"
                f"font-size:{fs:.1f}pt;"
                f"font-family:{font_name};"
                f"color:{f['color']};"
                f"padding:{padding_pt:.2f}pt;"
                f"letter-spacing:{letter_spacing_pt:.2f}pt;"
                f"opacity:{opacity:.3f};"
                f"white-space:{'pre-wrap' if multiline else 'nowrap'};"
                f"overflow:{'hidden' if multiline else 'visible'};"
                f"text-decoration:{' '.join(decorations) if decorations else 'none'};"
                f"background:{field_meta.get('bg_color', '#ffffff') if field_meta.get('bg_enabled') else 'transparent'};"
                f"text-align:{f['align']};"
                f"font-weight:{'bold' if f['font_bold'] else 'normal'};"
                f"font-style:{'italic' if f['font_italic'] else 'normal'};"
            )
            if f.get("field_type") == "barcode" and f.get("barcode_img"):
                # Render the actual barcode image, sized to the field box.
                bc_h_mm = max(float(f["height"] or 32), 16) * PT_TO_MM
                # Horizontal alignment of the (contained) barcode within its box.
                _al = (f["align"] or "left").lower()
                _justify = {"left": "flex-start", "center": "center",
                            "right": "flex-end"}.get(_al, "flex-start")
                _obj_pos = {"left": "left center", "center": "center center",
                            "right": "right center"}.get(_al, "left center")
                bc_style = (
                    f"left:{css_left_mm:.2f}mm;"
                    f"top:{(page_h - f['y'] - (f['height'] or 32)) * PT_TO_MM:.2f}mm;"
                    f"width:{css_width_mm:.2f}mm;"
                    f"height:{bc_h_mm:.2f}mm;"
                    # Flexbox so the crisp SVG barcode is scaled to fully fit the
                    # field box (whole symbol + human-readable caption visible),
                    # never truncated. The SVG's own viewBox + preserveAspectRatio
                    # keeps the bars razor-sharp at any DPI.
                    f"display:flex;align-items:center;justify-content:{_justify};"
                    f"overflow:visible;"
                )
                items_html.append(
                    f'<div class="ov-field ov-barcode" style="{bc_style}" '
                    f'data-field-key="{f["field_key"]}" data-label="{f["label"]}">'
                    f'<img src="{f["barcode_img"]}" alt="barcode" '
                    f'style="width:100%;height:100%;object-fit:contain;'
                    f'object-position:{_obj_pos};display:block;"></div>'
                )
                continue
            if f.get("field_type") == "trade_table":
                # Render the trade table as an HTML <table> matching the PDF
                # _draw_trade_table output (audit Fix 4). Column spec follows
                # the same convention: meta.columns is a CSV of keys, plus
                # built-in _sr / _assigned / _available.
                import html as _html_esc
                tt_meta = f.get("meta") or {}
                default_cols = [
                    ("_sr", "Sr#", "center"),
                    ("trade", "Trade / Visa Category", "left"),
                    ("quantity", "Qty", "center"),
                    ("_assigned", "Assigned", "center"),
                    ("_available", "Available", "center"),
                    ("salary", "Salary", "right"),
                    ("contract_period", "Contract", "center"),
                ]
                col_defs = tt_meta.get("columns")
                cols: list = []
                if isinstance(col_defs, list) and col_defs:
                    for spec in col_defs:
                        if isinstance(spec, dict):
                            cols.append((str(spec.get("key") or ""),
                                         str(spec.get("label") or spec.get("key") or ""),
                                         str(spec.get("align") or "left").lower()))
                elif isinstance(col_defs, str) and col_defs.strip():
                    defaults = {k: (lbl, al) for (k, lbl, al) in default_cols}
                    for k in [c.strip() for c in col_defs.split(",") if c.strip()]:
                        lbl, al = defaults.get(k, (k.replace("_", " ").title(), "left"))
                        cols.append((k, lbl, al))
                if not cols:
                    cols = default_cols
                show_header = bool(tt_meta.get("show_header", True))
                show_border = bool(tt_meta.get("border", True))
                header_fill = str(tt_meta.get("header_fill", "#e5e7eb"))
                zebra       = bool(tt_meta.get("zebra", False))
                rows_data = f.get("trade_rows") or []
                # Build the <table>
                tr_lines = []
                if show_header:
                    th_cells = "".join(
                        f'<th style="text-align:{al};background:{header_fill};'
                        f'border:{"1px solid #94a3b8" if show_border else "none"};'
                        f'padding:2px 4px;font-weight:700;">{_html_esc.escape(lbl)}</th>'
                        for (_k, lbl, al) in cols)
                    tr_lines.append(f"<tr>{th_cells}</tr>")
                for ri, row in enumerate(rows_data):
                    bg = "#f8fafc" if (zebra and ri % 2 == 1) else "transparent"
                    td_cells = []
                    for (k, _lbl, al) in cols:
                        v = row.get(k, "")
                        if k == "salary" and v not in (None, ""):
                            try:
                                v = f"{float(v):,.0f}"
                            except (TypeError, ValueError):
                                pass
                        td_cells.append(
                            f'<td style="text-align:{al};background:{bg};'
                            f'border:{"1px solid #94a3b8" if show_border else "none"};'
                            f'padding:2px 4px;">{_html_esc.escape("" if v is None else str(v))}</td>')
                    tr_lines.append(f"<tr>{''.join(td_cells)}</tr>")
                tt_style = (
                    f"left:{css_left_mm:.2f}mm;"
                    f"top:{(page_h - f['y'] - fld_h) * PT_TO_MM:.2f}mm;"
                    f"width:{css_width_mm:.2f}mm;"
                    f"font-size:{f['font_size']:.1f}pt;"
                    f"color:{f['color']};"
                )
                items_html.append(
                    f'<div class="ov-field ov-trade-table" style="position:absolute;{tt_style}" '
                    f'data-field-key="{f["field_key"]}" data-label="{f["label"]}">'
                    f'<table style="width:100%;border-collapse:collapse;font-family:Arial,Helvetica,sans-serif;">'
                    f'{"".join(tr_lines)}</table></div>'
                )
                continue
            import html as _field_html

            # --- char_cells: one character per pre-printed box -------------
            # BUG (audit 2026-08-11): this branch did not exist. Grid fields
            # (CNIC, passport no, phone, E-number) fell through to the plain
            # <div> below, so the printed page showed "3460117289185" as one
            # continuous string running across the form's 13 boxes instead of
            # one digit centred in each -- while the PDF engine, from the same
            # template, placed them correctly. The HTML path is the one the
            # Print button uses, so every printed form was wrong.
            #
            # Each character is now absolutely positioned at its own cell
            # centre using the SAME slot maths as the PDF engine (shared via
            # pdf_engine.cell_slots / normalize_cell_value), including
            # group_sizes support for the 5-7-1 CNIC grid.
            if f.get("field_type") == "char_cells":
                from app.services.pdf_engine import (
                    cell_slots as _cell_slots_pub,
                )
                from app.services.pdf_engine import (
                    coerce_group_sizes as _coerce_groups,
                )
                _meta = f.get("meta") or {}
                _raw = str(f["value"] or "")
                # ADDRESS CAPACITY POLICY -- see app/services/comb_capacity.py.
                #
                # This row's characters are decided by the SAME function the PDF
                # engine calls, using the real multi-row capacity discovered
                # from the sibling rows in `payload["fields"]`. Previously each
                # row normalised and sliced independently and truncated at its
                # own cell_count, so a 55-character address was cut mid-word at
                # 26 and the district vanished with no record of it.
                #
                # Routing both engines through one decision is the whole point:
                # they have already disagreed twice in this project's history
                # (the HTML path had no char_cells support at all until
                # 2026-08-11), and a divergence here means the Print button and
                # the PDF download produce different documents from one
                # template.
                from app.services.comb_capacity import (
                    resolve_comb_row as _resolve_comb_row,
                )
                _cv, _fit = _resolve_comb_row(
                    _raw, _meta,
                    sibling_fields=payload["fields"],
                    field_key=f.get("field_key") or "",
                    page=int(f.get("page") or 1),
                )
                if _fit.review_required:
                    logging.getLogger("dtc.documents").warning(
                        "comb capacity (%s): %s", f.get("field_key") or "?",
                        _fit.reason,
                    )
                _fw = float(f.get("width") or 0)
                _count = int(_meta.get("cell_count") or len(_cv) or 13)
                if _count <= 0:
                    _count = max(1, len(_cv))
                _gap = float(_meta.get("cell_gap") or 0)
                _groups = _coerce_groups(_meta.get("group_sizes"), _count)
                _cw, _clamped = _fitted_cell_pitch(
                    width=_fw, count=_count, gap=_gap,
                    stored=float(_meta.get("cell_width") or 0),
                    group_sizes=_groups,
                )
                if _clamped:
                    # Logged (not silently corrected) so the broken template is
                    # repairable in the Template Designer rather than papered
                    # over forever.
                    # NOTE: print_document() rebinds the name `log` to a
                    # GeneratedDocument row further down, shadowing the module
                    # logger, so reach for the logger explicitly here.
                    logging.getLogger("dtc.documents").warning(
                        "char_cells meta overflows its field box (template field "
                        "%r: %d cells x %.2fpt + gaps > width %.2fpt) -- falling "
                        "back to fitted pitch %.2fpt. Fix this field in the "
                        "Template Designer.",
                        f.get("field_key"), _count,
                        float(_meta.get("cell_width") or 0), _fw, _cw,
                    )
                _slots = _cell_slots_pub(_count, _groups)
                _spans = []
                for _i in range(min(_count, len(_cv))):
                    _left = _slots[_i] * (_cw + _gap)
                    # UNIT BUG (audit 2026-08-11, second pass): these offsets
                    # are PDF points, but were emitted as `px`. CSS defines
                    # 1px = 1/96in while 1pt = 1/72in, so every character grid
                    # printed at exactly 0.75x its intended pitch -- a 23.4pt
                    # CNIC comb came out at 17.55pt and the digits walked
                    # progressively left, up to 11.8pt (half a box) out of
                    # place by the last one. Everything else on this page is
                    # positioned in mm via PT_TO_MM; the spans must be too.
                    # Centre inside the cell, matching drawCentredString().
                    _spans.append(
                        f'<span style="position:absolute;'
                        f'left:{_left * PT_TO_MM:.3f}mm;'
                        f'width:{_cw * PT_TO_MM:.3f}mm;text-align:center;">'
                        f'{_field_html.escape(_cv[_i])}</span>'
                    )
                items_html.append(
                    f'<div class="ov-field ov-cells" style="{style}" '
                    f'data-field-key="{f["field_key"]}" data-label="{f["label"]}" '
                    f'data-cells="{_count}">{"".join(_spans)}</div>'
                )
                continue

            text = _field_html.escape(str(f["value"] or "")).replace("\n", "<br>") if multiline else _field_html.escape(str(f["value"] or ""))
            align_cls = "ov-" + (f["align"] if f["align"] in ("left", "center", "right") else "left")
            items_html.append(
                f'<div class="ov-field {align_cls}" style="{style}" data-field-key="{f["field_key"]}" '
                f'data-label="{f["label"]}">{text}</div>'
            )
        field_html_per_page.append((pn, "\n".join(items_html)))

    # Build the Content-tab notes block (audit Fix 3). The notes are stored
    # in tpl.description inside the designer meta-fence and must appear above
    # the page footer on every printed page, exactly matching the PDF
    # _draw_content_notes() implementation.
    notes_block_html = ""
    if content_notes:
        import html as _html
        notes_block_html = (
            '<div class="doc-notes">'
            + _html.escape(content_notes).replace("\n", "<br>")
            + '</div>'
        )

    # The letterhead is rendered as a CSS background-image on a dedicated
    # .doc-bg layer (NOT on .doc-page, NOT an <img>). Because that layer has no
    # print-color-adjust:exact override, the browser print panel's
    # "Background graphics" checkbox shows/hides it in real time -- exactly the
    # behaviour required for pre-printed form/letterhead paper.
    page_blocks = []
    for pn, items_html in field_html_per_page:
        _pbg = _bg_for_page(pn)
        if _pbg:
            doc_bg_html = (
                f'<div class="doc-bg" style="background-image: url(\'{_pbg}\');'
                f'{_bg_style_for(_pbg)}" data-bg="{_pbg}"></div>'
            )
            no_bg_html = ''
        else:
            doc_bg_html = ''
            no_bg_html = '<div class="no-bg">No background image set for this template.</div>'
        page_blocks.append(f'''
        <section class="doc-page" data-page="{pn}" {f'data-bg="{_pbg}"' if _pbg else ''}>
            {doc_bg_html}
            {no_bg_html}
            <div class="doc-overlay">
                {items_html}
            </div>
            {notes_block_html}
        </section>
        ''')

    record_label = ""
    if record:
        record_label = getattr(record, "full_name", None) or getattr(record, "file_number", None) or getattr(record, "company_name", None) or f"#{record_id}"

    auto_print_script = ""
    if auto:
        auto_print_script = """
        let _printed = false;
        function tryPrint(){
            if (_printed) return;
            _printed = true;
            // One animation frame is enough for the layout to settle; the
            // background is inlined as a base64 data: URI so it is already part
            // of the document and needs no network round-trip. This removes the
            // old 350ms + 3500ms stacked delays that made printing feel slow.
            requestAnimationFrame(() => { window.focus(); window.print(); });
        }
        // Backgrounds are inlined as data: URIs, so decoding is effectively
        // instant. We still wait for any <img>/CSS bg to be marked complete, but
        // with a MUCH tighter safety cap so the dialog appears promptly.
        const bgUrls = [...new Set(
            [...document.querySelectorAll('.doc-page[data-bg]')]
                .map(el => el.dataset.bg).filter(Boolean)
        )];
        if (bgUrls.length === 0) {
            tryPrint();
        } else {
            let loaded = 0;
            bgUrls.forEach(url => {
                const img = new Image();
                img.onload = img.onerror = () => { if (++loaded === bgUrls.length) tryPrint(); };
                img.src = url;
                if (img.complete) { img.onload = null; if (++loaded === bgUrls.length) tryPrint(); }
            });
            // Tight safety fallback (data: URIs decode near-instantly).
            setTimeout(tryPrint, 800);
        }
        """

    body_class = "fit-mode" if fit else ""
    # Fit-to-viewport scaler: when rendered inside the modal iframe, scale the
    # A4 page so it fills the iframe width. Print-mode CSS overrides this back
    # to true 1:1 A4 size, so the actual print output is unaffected -- the user
    # sees a properly-sized preview that matches what will physically print.
    fit_script = """
        (function(){
            var page = document.querySelector('.doc-page');
            if (!page) return;
            function apply(){
                // Fit the page to the available viewport width. Allow scaling
                // above 100% so the background image fills the preview area
                // instead of floating inside grey gutters. Cap at 150% to
                // prevent absurd upscaling on ultra-wide screens.
                var avail = document.documentElement.clientWidth - 16; // gutters
                document.body.style.setProperty('--fit-scale', '1');
                var pageW = page.getBoundingClientRect().width;
                if (pageW < 10) return;
                var scale = Math.max(0.4, Math.min(1.5, avail / pageW));
                document.body.style.setProperty('--fit-scale', String(scale));
            }
            apply();
            window.addEventListener('resize', apply);
            setTimeout(apply, 100);
            setTimeout(apply, 600);
        })();
    """ if fit else ""

    # CSP: CspNonceMiddleware enforces a nonce-based script-src, so the inline
    # <script> below (the letterhead toggle + auto-print + fit logic) must carry
    # the per-request nonce or the browser blocks it. Without this the in-app
    # "Show letterhead background" toggle silently fails.
    _nonce = getattr(getattr(request, "state", None), "csp_nonce", None)
    nonce_attr = f' nonce="{_nonce}"' if _nonce else ""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Print · {tpl.name} · {record_label}</title>
    <style{nonce_attr}>
        @page {{
            size: {page_size_css};
            margin: 0;
        }}
        * {{ box-sizing: border-box; }}
        html, body {{
            margin: 0; padding: 0; background: #525659;
            font-family: Arial, Helvetica, sans-serif;
        }}
        .toolbar {{
            position: sticky; top: 0; z-index: 1000;
            background: #323639; color: #fff; padding: 10px 16px;
            display: flex; align-items: center; justify-content: space-between;
            box-shadow: 0 2px 8px rgba(0,0,0,0.3);
        }}
        .toolbar h1 {{ font-size: 14px; margin: 0; font-weight: 600; }}
        .toolbar .sub {{ font-size: 11px; color: #ccc; margin-top: 2px; }}
        .toolbar button {{
            background: #0071e3; color: #fff; border: 0;
            padding: 9px 20px; border-radius: 10px; cursor: pointer;
            font-size: 13px; font-weight: 600; letter-spacing: -0.01em;
            display: inline-flex; align-items: center; gap: 6px;
            transition: background 150ms ease;
        }}
        .toolbar button:hover {{ background: #0058b9; }}
        .toolbar button.secondary {{ background: #555; }}
        .toolbar button.secondary:hover {{ background: #777; }}
        .doc-page {{
            position: relative;
            width: {page_w_mm:.2f}mm;
            height: {page_h_mm:.2f}mm;
            /* The white paper fill is plain -- NOT color-adjust:exact -- so it
               never fights the browser. The letterhead/form artwork lives on a
               dedicated .doc-bg layer below so the print panel's
               "Background graphics" checkbox can show/hide it in real time. */
            background: #fff;
            margin: 20px auto;
            box-shadow: 0 4px 16px rgba(0,0,0,0.4);
            overflow: hidden;
            page-break-after: always;
        }}
        /* Letterhead / pre-printed form artwork.  This is a genuine CSS
           background-image WITHOUT print-color-adjust:exact, so it obeys the
           browser print panel's "Background graphics" toggle:
             - checkbox ON  -> letterhead prints
             - checkbox OFF -> letterhead hidden in real time, only the field
                               overlay prints (for pre-printed paper stock).
           Screen preview always shows it via the @media screen rule. */
        .doc-bg {{
            position: absolute;
            inset: 0;
            width: 100%;
            height: 100%;
            /* background-size/position are a FALLBACK only -- each .doc-bg
               element carries an inline override produced by _bg_style_for(),
               which reproduces the template's bg_fit mode so this preview
               matches the designer canvas and the exported PDF exactly.
               See the E2 note next to _bg_style_for() in this module. */
            background-size: 100% 100%;
            background-repeat: no-repeat;
            background-position: top left;
            pointer-events: none;
            z-index: 0;
        }}
        /* On screen (preview modal + designer) always render the letterhead so
           the user sees the true document; the print toggle only affects paper. */
        @media screen {{
            .doc-bg {{
                -webkit-print-color-adjust: exact;
                print-color-adjust: exact;
                color-adjust: exact;
            }}
        }}
        /* === fit-to-viewport mode (used by in-app print-preview modal) ===
           The A4 page is exactly 209.9mm wide = ~793px at 96 DPI. Inside the
           modal iframe (which is ~1000px wide) the page would otherwise look
           small, floating, and "cropped" because of the gray modal margins.
           When fit=1 is requested, we use a CSS variable + transform: scale()
           to fluidly scale the page to ~96% of the iframe width while keeping
           the printed output (.doc-page in @media print below) at true 1:1 A4. */
        body.fit-mode {{
            background: #525659;
            min-height: 100vh;
            padding: 18px 0;
        }}
        body.fit-mode .toolbar {{ display: none !important; }}
        body.fit-mode .doc-page {{
            margin: 18px auto;
            /* JS sets --fit-scale = (viewport-width * 0.96) / page-pixel-width.
               Until JS runs, default to 1 so the layout never collapses. */
            transform: scale(var(--fit-scale, 1));
            transform-origin: top center;
        }}
        /* The transform shrinks the visual box but the layout box stays at full
           A4 size, so add a height correction wrapper-spacer via margin-bottom. */
        body.fit-mode .doc-page + .doc-page {{
            margin-top: calc(18px - ({page_h_mm:.2f}mm * (1 - var(--fit-scale, 1))));
        }}
        /* .doc-bg is no longer used -- background is now a CSS background-image
           on .doc-page so the browser "Background graphics" checkbox works. */
        .no-bg {{
            padding: 40px; text-align: center; color: #999;
            font-size: 14px;
        }}
        /* When printing on physical pre-printed letterhead, hide the
           "no background" placeholder so only field overlays print
           and align over the real letterhead paper. */
        @media print {{
            .no-bg {{ display: none !important; }}
        }}

        /* PRINT-FRAME RESET (audit 2026-08-11) -- phantom pages + wrong scale.
           The portal prints through an off-screen iframe which is still in
           SCREEN media when its raster box is fixed, so all of the on-screen
           viewing chrome was live at print time:
             - body.fit-mode padding:18px 0  and  .doc-page margin:18px auto
               pushed content past the A4 box (measured 23mm of overflow on a
               1-page document => a spooled blank second sheet), and
             - the fit-to-width `transform: scale(--fit-scale)` is a VIEWING
               aid; leaving it applied while printing rescales the page so the
               mm-precise field coordinates no longer land on the pre-printed
               form. That is fatal for this engine, whose whole job is putting
               text at exact millimetre positions.
           Resetting these under .print-frame restores a true 1:1 A4 page box at
           first layout. Screen viewing keeps fit-mode exactly as before. */
        html.print-frame, html.print-frame body {{
            margin: 0 !important; padding: 0 !important; background: #fff !important;
        }}
        html.print-frame body.fit-mode {{ padding: 0 !important; background: #fff !important; }}
        html.print-frame .toolbar {{ display: none !important; }}
        html.print-frame .doc-page,
        html.print-frame body.fit-mode .doc-page {{
            margin: 0 !important;
            box-shadow: none !important;
            transform: none !important;   /* print at true 1:1 -- never scaled */
        }}
        html.print-frame body.fit-mode .doc-page + .doc-page {{
            margin-top: 0 !important;
        }}
        .doc-overlay {{
            position: absolute; top: 0; left: 0;
            width: 100%; height: 100%;
            z-index: 1;
            /* Keep exact colour rendering for the text/field content only, so
               dark field text always prints crisply regardless of the
               "Background graphics" toggle (which governs .doc-bg alone). */
            -webkit-print-color-adjust: exact !important;
            print-color-adjust: exact !important;
            color-adjust: exact !important;
        }}
        .ov-field {{
            position: absolute;
            font-family: Arial, Helvetica, sans-serif;
            white-space: nowrap;
            overflow: visible;
            /* BASELINE-LOCKED single-line box. The element is exactly one line
               tall (height = line-height = font_size) and its top is computed
               server-side so the typographic baseline coincides with the PDF
               drawString baseline. No flex / no leading can shift the glyphs,
               so designer canvas == this print view == the exported PDF. */
            padding: 0;
            margin: 0;
            box-sizing: content-box;
        }}
        .ov-field.ov-left   {{ text-align: left; }}
        .ov-field.ov-center {{ text-align: center; }}
        .ov-field.ov-right  {{ text-align: right; }}
        /* char_cells (audit 2026-08-11): each character is absolutely placed
           at its own cell centre by the emitter, so the container must be a
           positioning context and must NOT re-align its children -- any
           text-align here would fight the per-cell placement. */
        .ov-field.ov-cells {{ text-align: left; position: absolute; }}
        .ov-field.ov-cells > span {{ display: inline-block; top: 0; }}
        /* Content-tab notes -- printed at the bottom of every page above the
           browser footer, mirroring pdf_engine._draw_content_notes. */
        .doc-notes {{
            position: absolute;
            left: 30px;
            right: 30px;
            bottom: 30px;
            font-family: Arial, Helvetica, sans-serif;
            font-style: italic;
            font-size: 8pt;
            color: #475569;
            line-height: 1.35;
            white-space: pre-wrap;
        }}
        @media print {{
            html, body {{
                background: #fff !important;
                margin: 0 !important;
                padding: 0 !important;
                width: {page_w_mm:.2f}mm !important;
                /* Lock the print zoom so MOBILE Chrome can't apply its own
                   "fit to page" scaling (the root cause of the phone-vs-PC
                   mismatch). With an explicit mm @page + 1:1 mm page box, the
                   output is now identical on desktop and mobile. */
                zoom: 1 !important;
                -webkit-text-size-adjust: 100% !important;
                text-size-adjust: 100% !important;
            }}
            .toolbar {{ display: none !important; }}
            /* Reset the fit-mode transform so printed output is true 1:1 A4
               size -- exactly matching the designer canvas, no scaling. Pin the
               page box to the EXACT mm dimensions so neither desktop nor mobile
               can rescale it. NOTE: .doc-page deliberately has NO
               print-color-adjust:exact -- the letterhead lives on .doc-bg and
               must obey the "Background graphics" toggle. */
            body.fit-mode .doc-page,
            .doc-page {{
                margin: 0 !important;
                padding: 0 !important;
                width: {page_w_mm:.2f}mm !important;
                height: {page_h_mm:.2f}mm !important;
                box-shadow: none !important;
                transform: none !important;
                page-break-after: always;
                break-after: page;
                overflow: hidden !important;
                background: #fff !important;
            }}
            /* Letterhead layer keeps its 100%x100% sizing on print, but WITHOUT
               forcing color-adjust:exact so the user can disable it from the
               print panel. When "Background graphics" is unchecked the browser
               drops this layer in real time and only the overlay prints. */
            .doc-bg {{
                background-size: 100% 100% !important;
                background-repeat: no-repeat !important;
            }}
            .doc-page:last-child {{ page-break-after: auto; break-after: auto; }}
        }}
    </style>
</head>
<body class="{body_class}">
    <div class="toolbar">
        <div>
            <h1>{tpl.name}</h1>
            <div class="sub">Record: {record_label or '-- blank preview --'} · {len(payload["fields"])} mapped fields · {len(page_numbers)} page(s)</div>
        </div>
        <div style="display:flex; gap:12px; align-items:center;">
            <label style="font-size:12px; color:#eee; display:inline-flex; align-items:center; gap:6px; cursor:pointer; user-select:none;">
                <input type="checkbox" id="bg-toggle" checked style="width:15px;height:15px;cursor:pointer;">
                Show letterhead background
            </label>
            <span style="font-size:11px; color:#aaa; max-width:300px; line-height:1.35;">
                💡 Or use the browser print panel's <b>More settings → Background graphics</b>
                checkbox -- unchecking it prints only the data on pre-printed paper.
            </span>
            <button class="secondary" onclick="window.close()">Close</button>
            <button onclick="window.print()">🖨️ Print Now</button>
        </div>
    </div>

    {''.join(page_blocks)}

    <script{nonce_attr}>
        // In-app letterhead toggle -- live show/hide of the .doc-bg layer so
        // the user can preview exactly what prints when "Background graphics"
        // is off, without digging into browser settings.
        (function(){{
            var cb = document.getElementById('bg-toggle');
            if (!cb) return;
            function apply(){{
                var show = cb.checked;
                document.querySelectorAll('.doc-bg').forEach(function(el){{
                    el.style.display = show ? '' : 'none';
                }});
            }}
            cb.addEventListener('change', apply);
            apply();
        }})();
        {auto_print_script}
        {fit_script}
    </script>
</body>
</html>
"""
    # Zero-exception CSP: this page positions every overlay field with a
    # runtime-computed inline style ATTRIBUTE (absolute mm coordinates, barcode
    # boxes, the base64 background layer), which CSP cannot nonce. Move them
    # into a nonced <style> element so the response is served with
    # style-src-attr 'none'. Layout code above is untouched; the page's own JS
    # only mutates style through the CSSOM (el.style.*), which CSP does not
    # police, so nothing breaks at runtime. See app/core/style_hoist.py.
    from app.core.style_hoist import hoist_inline_styles
    return HTMLResponse(hoist_inline_styles(html, _nonce or ""))
