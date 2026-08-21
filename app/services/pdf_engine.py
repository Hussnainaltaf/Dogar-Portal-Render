"""PDF Overlay Engine v2 -- Professional Coordinate-Based Document Renderer.

Generates a filled PDF by overlaying coordinate-mapped fields on top of a real
document background image (visa form, bank slip, demand letter, etc.).

Supported field types (DocumentField.field_type):
    text        - plain Latin text
    arabic      - Arabic text (RTL, with proper shaping via arabic-reshaper + python-bidi)
    date        - same as text, but value is auto-formatted
    checkbox    - draws a tick / X mark if value is truthy
    static      - prints static_value verbatim
    char_cells  - one digit per cell across N evenly-spaced boxes
                  (use meta={"cell_count":13,"cell_width":18,"cell_gap":2})
    photo       - clips candidate.photo into the (x,y,width,height) box
                  (use meta={"fit":"cover","border":true})
    barcode     - Code128 barcode of the resolved value
    trade_table - auto-builds the Visa Category / Qty / Assigned / Available table

Key guarantees:
- ALWAYS produces a valid multi-page PDF (>= 1 page), even when the template
  has zero placed fields (auto-grid fallback).
- Real Arabic glyphs render correctly (no more tofu boxes).
- Candidate photo is clipped to the photo-box on OEP forms.
- Character-cell rows (CNIC 13-digit, passport, phone) auto-distribute.
"""
import io
import logging
import os
import re
from datetime import date, datetime
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import code39, code128, eanbc
from reportlab.graphics.barcode import qr as _qr
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.shapes import Drawing as _QRDrawing
from reportlab.lib.colors import Color, HexColor, black, white
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from app.core.config import settings
from app.core.geometry_contract import resolve_page_size
from app.models import (
    Candidate,
    Client,
    Demand,
    DocumentField,
    DocumentTemplate,
)
from app.services.comb_capacity import resolve_comb_row

# AUDIT FIX (2026-07-07): module-level structured logger (matches the
# "dtc.<module>" convention used across the app, e.g. dtc.email/dtc.audit).
# Replaces the bare debug print() calls that previously sat inside the
# exception handlers below — the exception-handling logic is unchanged,
# only the reporting call now goes through the logging framework.
log = logging.getLogger("dtc.pdf_engine")

# Background filenames already reported as unresolvable (see
# _resolve_background_path). Bounded by the number of distinct template
# backgrounds, so it cannot grow without limit, and keeps a bulk generation run
# from repeating the same warning once per document.
_MISSING_BG_WARNED: set[str] = set()


# ---------------------------------------------------------------------------
# Process-level image cache: background images and candidate photos are
# typically ~1-6 MB JPEG/PNG files that the PDF engine re-opens and re-compresses
# on EVERY page of EVERY document.  A process-level LRU cache keyed by
# (path, mtime, fit) eliminates the repeated disk I/O and PIL decode/encode
# cycle.  Max 32 entries ≈ ~50-200 MB RAM ceiling — acceptable for a portal
# that typically has <20 distinct background scans.  Cache is invalidated
# automatically when the file modification time changes (new upload).
#
# Thread-safety: io.BytesIO buffers are NOT shared — each cache hit copies
# the cached bytes into a fresh BytesIO before wrapping with ImageReader.
# ---------------------------------------------------------------------------
@lru_cache(maxsize=32)
def _cached_bg_bytes(path: str, mtime: float, fit: str) -> bytes:
    """Return the processed image bytes for a background file (cached by path+mtime+fit)."""
    img = Image.open(path)
    iw, ih = img.size
    buf = io.BytesIO()
    if fit == "fill":
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=88, optimize=True)
    else:
        if img.mode not in ("RGB", "RGBA", "L", "1"):
            img = img.convert("RGBA")
        img.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), iw, ih


@lru_cache(maxsize=128)
def _cached_photo_bytes(path: str, mtime: float,
                        crop_left: int, crop_top: int,
                        crop_right: int, crop_bottom: int) -> bytes:
    """Return JPEG bytes for a candidate photo, cropped to a specific box (cached)."""
    img = Image.open(path)
    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")
    if crop_right > crop_left and crop_bottom > crop_top:
        img = img.crop((crop_left, crop_top, crop_right, crop_bottom))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88, optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Arabic shaping pipeline -- reshapes Arabic so glyphs join correctly, then
# applies BiDi algorithm for proper right-to-left visual order.
# ---------------------------------------------------------------------------
try:
    import arabic_reshaper
    from bidi.algorithm import get_display
    _ARABIC_LIBS_OK = True
except Exception as e:
    log.warning("arabic libs not available: %s", e)
    _ARABIC_LIBS_OK = False


_ARABIC_RANGES = [
    (0x0600, 0x06FF),  # Arabic
    (0x0750, 0x077F),  # Arabic Supplement
    (0x08A0, 0x08FF),  # Arabic Extended-A
    (0xFB50, 0xFDFF),  # Arabic Presentation Forms-A
    (0xFE70, 0xFEFF),  # Arabic Presentation Forms-B
]


# ---------------------------------------------------------------------------
# Value pre-formatters used by char_cells (set via field.meta["format"])
# ---------------------------------------------------------------------------
_MONTH_MAP = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "sept": "09", "oct": "10", "nov": "11", "dec": "12",
}


def _format_date_ddmmyyyy(value: str) -> str:
    """Normalize a date-like string into a compact DDMMYYYY digit run.

    Accepts a range of common inputs (already-numeric or with separators
    or month names) and returns an 8-char digit string when possible.

        "13-Feb-2015"   -> "13022015"
        "13-02-2015"    -> "13022015"
        "2015-02-13"    -> "13022015"
        "13/02/15"      -> "13022015"
        "13022015"      -> "13022015"
        ""              -> ""
    """
    if not value:
        return ""
    s = str(value).strip()
    if not s:
        return ""

    # Already an 8-digit run? (DDMMYYYY)
    digits_only = "".join(ch for ch in s if ch.isdigit())
    if len(digits_only) == 8 and "-" not in s and "/" not in s and " " not in s:
        return digits_only

    # Token-based parsing
    import re
    tokens = [t for t in re.split(r"[^A-Za-z0-9]+", s) if t]

    day = month = year = ""
    if len(tokens) >= 3:
        t0, t1, t2 = tokens[0], tokens[1], tokens[2]
        # Detect ISO YYYY-MM-DD
        if t0.isdigit() and len(t0) == 4:
            year, month, day = t0, t1, t2
        else:
            day, month, year = t0, t1, t2

        # Resolve month name if needed
        m_low = month.lower()[:4]
        if not month.isdigit():
            for key, num in _MONTH_MAP.items():
                if m_low.startswith(key):
                    month = num
                    break

        # Zero-pad
        if day.isdigit():
            day = day.zfill(2)
        if month.isdigit():
            month = month.zfill(2)
        # Two-digit year -> 20xx
        if year.isdigit() and len(year) == 2:
            yr = int(year)
            year = ("20" if yr < 50 else "19") + year

        if day.isdigit() and month.isdigit() and year.isdigit() and len(year) == 4:
            return f"{day}{month}{year}"

    # Fallback: best-effort numeric extraction
    return digits_only[:8]


def _has_arabic(text: str) -> bool:
    if not text:
        return False
    for ch in text:
        cp = ord(ch)
        for lo, hi in _ARABIC_RANGES:
            if lo <= cp <= hi:
                return True
    return False


def _looks_non_latin(text: str) -> bool:
    """True if text has chars Helvetica cannot render (Arabic/CJK/etc)."""
    if not text:
        return False
    for ch in text:
        if ord(ch) > 0x024F:
            return True
    return False


def shape_arabic(text: str) -> str:
    """Reshape Arabic text + apply BiDi so it renders correctly in a
    LTR-only PDF library (ReportLab). Falls back to original text if libs
    are missing or text contains no Arabic."""
    if not text or not _ARABIC_LIBS_OK or not _has_arabic(text):
        return text
    try:
        reshaped = arabic_reshaper.reshape(text)
        return get_display(reshaped)
    except Exception as e:
        log.warning("arabic shaping failed: %s", e)
        return text


# ---------------------------------------------------------------------------
# Font registration -- supports DejaVu (Latin+general Unicode) + Noto Sans/Naskh
# Arabic (proper Arabic glyphs).
# ---------------------------------------------------------------------------
_FONTS_REGISTERED = False
_FONT_FLAGS = {
    "dejavu": False,
    "dejavu_bold": False,
    "noto_arabic": False,
    "noto_arabic_bold": False,
    "noto_naskh": False,
    "noto_naskh_bold": False,
}


def _font_dir():
    for d in [
        os.path.join("app", "static", "fonts"),
        os.path.join(os.path.dirname(__file__), "..", "static", "fonts"),
    ]:
        if os.path.isdir(d):
            return d
    return None


def _register_fonts():
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return
    _FONTS_REGISTERED = True
    d = _font_dir()
    if not d:
        log.warning("no font dir found")
        return

    registrations = [
        ("DejaVuSans", "DejaVuSans.ttf", "dejavu"),
        ("DejaVuSans-Bold", "DejaVuSans-Bold.ttf", "dejavu_bold"),
        ("NotoSansArabic", "NotoSansArabic-Regular.ttf", "noto_arabic"),
        ("NotoSansArabic-Bold", "NotoSansArabic-Bold.ttf", "noto_arabic_bold"),
        ("NotoNaskhArabic", "NotoNaskhArabic-Regular.ttf", "noto_naskh"),
        ("NotoNaskhArabic-Bold", "NotoNaskhArabic-Bold.ttf", "noto_naskh_bold"),
    ]
    for name, filename, flag in registrations:
        path = os.path.join(d, filename)
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont(name, path))
                _FONT_FLAGS[flag] = True
            except Exception as e:
                log.warning("register %s failed: %s", name, e)


_register_fonts()


@lru_cache(maxsize=256)
def _font_name(bold: bool = False, italic: bool = False, text: str = "",
               prefer_arabic: bool = False, custom_font_name: str = "") -> str:
    """Pick the right font family for the given text.

    - If text contains Arabic → Noto Sans Arabic (or Naskh) if registered.
    - Otherwise if non-Latin → DejaVuSans (Unicode coverage).
    - Otherwise, if custom_font_name is provided → custom_font_name.
    - Otherwise → Helvetica (built-in).
    """
    if prefer_arabic or _has_arabic(text):
        if bold and _FONT_FLAGS["noto_arabic_bold"]:
            return "NotoSansArabic-Bold"
        if _FONT_FLAGS["noto_arabic"]:
            return "NotoSansArabic"
        if bold and _FONT_FLAGS["noto_naskh_bold"]:
            return "NotoNaskhArabic-Bold"
        if _FONT_FLAGS["noto_naskh"]:
            return "NotoNaskhArabic"
        # Fall through to DejaVu (no Arabic shaping but won't crash)
    if _looks_non_latin(text):
        if bold and _FONT_FLAGS["dejavu_bold"]:
            return "DejaVuSans-Bold"
        if _FONT_FLAGS["dejavu"]:
            return "DejaVuSans"
    # Try custom font name. The designer stores human font names (Arial,
    # Georgia, Verdana, ...) which are NOT ReportLab identifiers, so map them
    # to the closest registered/standard face. Bold/italic variants are honored
    # where the base 14 provides them. Unknown names fall through to Helvetica.
    if custom_font_name:
        mapped = _map_custom_font(custom_font_name, bold=bold, italic=italic)
        if mapped:
            return mapped
    # Pure Latin
    if bold and italic:
        return "Helvetica-BoldOblique"
    if bold:
        return "Helvetica-Bold"
    if italic:
        return "Helvetica-Oblique"
    return "Helvetica"


# Human font-name -> ReportLab family. Values are the *base* family; the
# _map_custom_font helper appends the right Bold/Italic suffix for the base 14.
_CUSTOM_FONT_FAMILY = {
    "arial": "Helvetica",
    "helvetica": "Helvetica",
    "verdana": "Helvetica",
    "tahoma": "Helvetica",
    "times new roman": "Times",
    "times": "Times",
    "georgia": "Times",
    "courier new": "Courier",
    "courier": "Courier",
    # Unicode / Arabic capable (registered TTFs, no synthetic bold/italic)
    "noto arabic": "NotoSansArabic",
    "notosansarabic": "NotoSansArabic",
    "dejavu sans": "DejaVuSans",
    "dejavusans": "DejaVuSans",
}


def _map_custom_font(name: str, *, bold: bool = False, italic: bool = False) -> str:
    """Resolve a designer font name to a valid ReportLab font identifier."""
    raw = (name or "").strip()
    if not raw:
        return ""
    family = _CUSTOM_FONT_FAMILY.get(raw.lower())
    # If the caller already passed a real ReportLab id (e.g. legacy
    # "Times-Roman", "Helvetica-Bold", "DejaVuSans-Bold"), keep it verbatim.
    if not family:
        return raw
    if family == "Times":
        if bold and italic:
            return "Times-BoldItalic"
        if bold:
            return "Times-Bold"
        if italic:
            return "Times-Italic"
        return "Times-Roman"
    if family == "Courier":
        if bold and italic:
            return "Courier-BoldOblique"
        if bold:
            return "Courier-Bold"
        if italic:
            return "Courier-Oblique"
        return "Courier"
    if family == "Helvetica":
        if bold and italic:
            return "Helvetica-BoldOblique"
        if bold:
            return "Helvetica-Bold"
        if italic:
            return "Helvetica-Oblique"
        return "Helvetica"
    if family == "NotoSansArabic":
        if bold and _FONT_FLAGS.get("noto_arabic_bold"):
            return "NotoSansArabic-Bold"
        if _FONT_FLAGS.get("noto_arabic"):
            return "NotoSansArabic"
        return ""  # not registered → let caller fall back
    if family == "DejaVuSans":
        if bold and _FONT_FLAGS.get("dejavu_bold"):
            return "DejaVuSans-Bold"
        if _FONT_FLAGS.get("dejavu"):
            return "DejaVuSans"
        return ""
    return family


# ---------------------------------------------------------------------------
# Helpers -- colors, record resolution, background loading
# ---------------------------------------------------------------------------
def _hex_to_color(hexcode: str):
    try:
        return HexColor(hexcode)
    except Exception:
        return black


def _resolve_record_value(record: Any, field_key: str) -> str:
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


def _load_record(db, data_source: str, record_id: Optional[int]):
    if not record_id:
        return None
    mapping = {
        "candidate": Candidate,
        "demand": Demand,
        "client": Client,
        # REMOVED 2026-07-30: "agent": Agent -- the Sub-Agents feature and its
        # model were deleted on the operator's request (see section 4.11 of
        # PROJECT_DOCUMENTATION.md). An "agent" data_source now falls through
        # to the None return below, i.e. renders as a template with no record.
    }
    model = mapping.get(data_source)
    if not model:
        return None
    return db.query(model).filter(model.id == record_id).first()


def _resolve_background_path(template: DocumentTemplate) -> Optional[str]:
    bg = (template.background_image or "").strip().replace("\\", "/")
    if not bg:
        return None
    lower = bg.lower()
    for prefix in ("/static/pdf_backgrounds/", "static/pdf_backgrounds/"):
        if lower.startswith(prefix):
            bg = bg[len(prefix):]
            break
    # SECURITY (audit H-1, 2026-07-10): collapse to a bare filename before
    # joining with any backgrounds directory. Previously this resolver also
    # honoured absolute paths and left "../" intact, allowing a tampered
    # template.background_image to read arbitrary files. Only a leaf filename
    # that resolves *inside* one of the known backgrounds dirs is accepted.
    bg = os.path.basename(bg.rsplit("/", 1)[-1])
    if bg in ("", ".", ".."):
        return None
    roots = [
        os.path.realpath(settings.PDF_BG_DIR),
        os.path.realpath(os.path.join("app", "static", "pdf_backgrounds")),
        os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "static", "pdf_backgrounds")),
    ]
    for root in roots:
        path = os.path.realpath(os.path.join(root, bg))
        # Enforce the directory boundary.
        if path != root and not path.startswith(root + os.sep):
            continue
        if os.path.exists(path) and os.path.getsize(path) > 200:
            return path

    # A template that NAMES a background but whose file cannot be found renders
    # as blank paper with the overlay text floating on it. That is visually
    # obvious on a bank form, yet the code returned None without a word, so the
    # only way to discover it was for someone to look at the output. An audit
    # found exactly this case (permission_undertaking_1.jpg, declared in
    # form_schema.py and never shipped). Warn, and name the file and the
    # directory searched so the fix is mechanical.
    if bg not in _MISSING_BG_WARNED:
        _MISSING_BG_WARNED.add(bg)
        log.warning(
            "background image %r for template_id=%s could not be resolved in %s "
            "-- the document will render WITHOUT its background artwork",
            bg, getattr(template, "id", "?"), settings.PDF_BG_DIR,
        )
    return None


def _bg_placement(fit: str, iw: int, ih: int, w: float, h: float):
    """Return (x, y, draw_w, draw_h) in PDF points for a background image of
    intrinsic size (iw, ih) placed on a page of size (w, h) under a given fit
    mode. The page origin is bottom-left (ReportLab convention).

    fill    – stretch to the full page (may distort).
    contain – scale down to fit inside the page keeping aspect ratio, pinned to
              the TOP-left (so it lines up with the on-screen designer).
    actual  – 1px = 1pt, pinned top-left, no scaling (most faithful).
    """
    if fit not in ("contain", "actual") or not iw or not ih:
        return 0.0, 0.0, w, h
    if fit == "actual":
        dw, dh = float(iw), float(ih)
    else:  # contain
        scale = min(w / iw, h / ih)
        dw, dh = iw * scale, ih * scale
    x = 0.0
    y = h - dh  # top-left anchor: image sits at the top of the page
    return x, y, dw, dh


def _draw_background(c: canvas.Canvas, bg_path: Optional[str], w: float, h: float,
                     fit: str = "fill"):
    if not bg_path:
        return False
    fit = fit if fit in ("fill", "contain", "actual") else "fill"
    try:
        # Use cached bytes keyed by (path, mtime, fit) — avoids re-opening +
        # re-compressing the same background image on every page / every call.
        mtime = os.path.getmtime(bg_path)
        raw, iw, ih = _cached_bg_bytes(bg_path, mtime, fit)
        buf = io.BytesIO(raw)   # fresh buffer each call — ImageReader is not thread-safe to share
        x, y, dw, dh = _bg_placement(fit, iw, ih, w, h)
        c.drawImage(ImageReader(buf), x, y, width=dw, height=dh,
                    preserveAspectRatio=(fit != "fill"), mask="auto")
        return True
    except Exception as e:
        try:
            c.drawImage(bg_path, 0, 0, width=w, height=h,
                        preserveAspectRatio=(fit != "fill"))
            return True
        except Exception:
            log.warning("background draw failed for %s: %s", bg_path, e)
            return False


# ---------------------------------------------------------------------------
# Photo overlay -- clips candidate photo into a box on the background
# ---------------------------------------------------------------------------
def _resolve_photo_path(record: Any, field_key: str = "photo") -> Optional[str]:
    """Find the photo file for the given record. Tries common photo columns."""
    if record is None:
        return None
    # Try the explicit field_key first, then fallbacks
    for key in (field_key, "photo", "photo_path", "image", "profile_photo"):
        p = getattr(record, key, None)
        if not p:
            continue
        # Normalize stored path → absolute file path
        p = str(p).strip()
        if not p or p in (".", "/", "static", "/static"):
            continue
        if p.startswith("/static/"):
            p = p[1:]
        if p.startswith("http://") or p.startswith("https://"):
            continue  # external URL, skip for now
        basename = os.path.basename(p)
        if not basename:
            continue
        candidates = [
            p,
            os.path.join("app", p),
            os.path.join("app", "static", "uploads", basename),
            os.path.join(os.path.dirname(__file__), "..", p),
            os.path.join(os.path.dirname(__file__), "..", "static", "uploads", basename),
        ]
        for cand in candidates:
            if (cand
                    and os.path.exists(cand)
                    and os.path.isfile(cand)
                    and os.path.getsize(cand) > 200):
                return cand
    return None


def _draw_photo(c: canvas.Canvas, photo_path: str, x: float, y: float,
                w: float, h: float, fit: str = "cover", border: bool = True):
    """Draw the candidate photo, clipped to (x, y, w, h). Renders a
    placeholder box if the photo is missing or unreadable."""
    if not photo_path or not os.path.isfile(photo_path):
        c.setStrokeColor(HexColor("#9ca3af"))
        c.setLineWidth(0.5)
        c.rect(x, y, w, h, stroke=1, fill=0)
        c.setFont("Helvetica-Oblique", 7)
        c.setFillColor(HexColor("#9ca3af"))
        c.drawCentredString(x + w / 2, y + h / 2, "[photo]")
        return False
    try:
        # Compute crop coordinates up-front so they can be cache-keyed.
        img_probe = Image.open(photo_path)
        iw, ih = img_probe.size
        img_probe.close()
        target_ratio = w / h
        src_ratio = iw / ih
        crop_left = crop_top = 0
        crop_right, crop_bottom = iw, ih
        if fit == "cover":
            if src_ratio > target_ratio:
                new_w = int(ih * target_ratio)
                crop_left = (iw - new_w) // 2
                crop_right = crop_left + new_w
            elif src_ratio < target_ratio:
                new_h = int(iw / target_ratio)
                crop_top = (ih - new_h) // 2
                crop_bottom = crop_top + new_h
        mtime = os.path.getmtime(photo_path)
        raw = _cached_photo_bytes(photo_path, mtime,
                                  crop_left, crop_top, crop_right, crop_bottom)
        buf = io.BytesIO(raw)
        c.drawImage(ImageReader(buf), x, y, width=w, height=h,
                    preserveAspectRatio=False, mask="auto")
        if border:
            c.setStrokeColor(HexColor("#1f2937"))
            c.setLineWidth(0.6)
            c.rect(x, y, w, h, stroke=1, fill=0)
        return True
    except Exception as e:
        log.warning("photo draw failed: %s", e)
        c.setStrokeColor(HexColor("#9ca3af"))
        c.setLineWidth(0.5)
        c.rect(x, y, w, h, stroke=1, fill=0)
        c.setFont("Helvetica-Oblique", 7)
        c.setFillColor(HexColor("#9ca3af"))
        c.drawCentredString(x + w / 2, y + h / 2, "[photo]")
        return False


# ---------------------------------------------------------------------------
# Character cells -- auto-distribute one digit per cell
# ---------------------------------------------------------------------------
def _cell_slots(cell_count: int, group_sizes: Optional[List[int]]) -> List[int]:
    """Map each character index to the grid slot it should occupy.

    Without ``group_sizes`` this is simply ``[0, 1, 2, ...]`` -- an evenly
    spaced run of cells, which is what every caller got before this existed.

    Pre-printed government forms rarely use one even run, though. A Pakistani
    CNIC grid is printed as 5-7-1 with a visibly wider gap where the dashes go
    (``34102-3634966-1``). Modelling that as 13 even cells cannot work: by the
    last digit the accumulated error is two whole cells wide. Passing
    ``group_sizes=[5, 7, 1]`` inserts one blank slot between groups, so the
    characters land in the printed boxes while the value itself stays a plain
    13-digit string.

    A malformed ``group_sizes`` (not summing to ``cell_count``) is ignored
    rather than raising: a bad value in a template's JSON meta must not stop a
    document from being generated.
    """
    if not group_sizes or sum(group_sizes) != cell_count:
        return list(range(cell_count))

    slots: List[int] = []
    slot = 0
    for group_index, size in enumerate(group_sizes):
        if group_index:
            slot += 1  # the separator gap the form prints between groups
        for _ in range(size):
            slots.append(slot)
            slot += 1
    return slots


def normalize_cell_value(value: Any, fmt: str = "") -> str:
    """Apply a char_cells `format` rule, then strip separators.

    SHARED (audit 2026-08-11): the HTML print path -- the one the portal's
    Print button actually uses -- had NO char_cells implementation at all. It
    emitted the raw value into a single <div>, so a 13-digit CNIC printed as
    one continuous string straight across the form's 13 pre-printed boxes
    instead of one digit per box. The PDF engine did it correctly, so the two
    outputs of the same template disagreed.

    Rather than reimplement (and let the two drift apart), the value-shaping
    and slot-allocation rules are exported here and consumed by BOTH engines.
    Any future format rule therefore lands on paper identically whichever
    path produced it.

    WORD-RUN-TOGETHER BUG (audit 2026-08-12): the separator strip below removes
    SPACES too, which is right for an ID grid (an operator typing
    "34601-1728918-5" or "PM 5199182" must not waste a cell on punctuation) but
    wrong for a NAME or ADDRESS comb, where the printed form expects one letter
    per box and a BLANK box between words. "MUHAMMAD IMRAN" was landing as
    "MUHAMMADIMRAN" -- every letter after the space shifted one box left, and on
    a two-row comb the continuation row inherited the shift.

    Rather than change what ``format: ""`` means (the phone/CNIC/passport grids
    in production rely on it, and silently altering them would be exactly the
    kind of invisible regression this module keeps getting bitten by), spaces
    are preserved only for the explicit ``format: "text"``.
    """
    text = str(value or "")
    f = (fmt or "").lower()
    if f == "ddmmyyyy":
        text = _format_date_ddmmyyyy(text)
    elif f == "digits":
        text = "".join(ch for ch in text if ch.isdigit())
    elif f == "alnum":
        text = "".join(ch for ch in text if ch.isalnum())
    elif f == "text":
        # One character per box INCLUDING spaces: collapse runs of whitespace
        # to a single space (a double space on paper is always a typo, and it
        # would silently shift the rest of the row) but keep the word breaks.
        return re.sub(r"\s+", " ", text).strip()
    # Mirrors _draw_char_cells(): a grid holds bare characters, so the
    # separators an operator typed ("34601-1728918-5") never occupy a cell.
    return text.strip().replace("-", "").replace(" ", "")


def coerce_group_sizes(raw: Any, cell_count: int) -> Optional[List[int]]:
    """Validate operator-editable `group_sizes` JSON (e.g. CNIC [5,7,1]).

    Returns None for anything malformed so the caller falls back to an even
    run: a bad value in a template's meta must never stop a document printing.
    """
    if not isinstance(raw, list):
        return None
    try:
        sizes = [int(n) for n in raw]
    except (TypeError, ValueError):
        return None
    if not sizes or any(n <= 0 for n in sizes) or sum(sizes) != cell_count:
        return None
    return sizes


def cell_slots(cell_count: int, group_sizes: Optional[List[int]] = None) -> List[int]:
    """Public wrapper over :func:`_cell_slots` for non-PDF renderers."""
    return _cell_slots(cell_count, group_sizes)


def grid_slot_span(cell_count: int, group_sizes: Any = None) -> int:
    """How many *slot positions* wide a ``char_cells`` grid really is.

    Usually this equals ``cell_count`` -- one slot per character. A grouped grid
    (a CNIC printed 5-7-1) also consumes one blank slot per separator, so 13
    characters occupy 15 slots. Measuring by characters under-measures such a
    grid by two whole cells, which makes an overflow check a false negative and
    a refit actively wrong (dividing the box by 13 instead of 15 pushes every
    digit after the first dash out of its printed box).
    """
    sizes = coerce_group_sizes(group_sizes, cell_count)
    if not sizes:
        return max(0, int(cell_count or 0))
    slots = _cell_slots(int(cell_count or 0), sizes)
    return (max(slots) + 1) if slots else max(0, int(cell_count or 0))


def fitted_cell_pitch(width: float, cell_count: int, cell_gap: float,
                      stored_cell_width: float,
                      group_sizes: Any = None) -> Tuple[float, bool]:
    """Resolve the per-cell pitch for a ``char_cells`` field.

    Returns ``(pitch, was_clamped)``.

    A template may store a ``cell_width`` that cannot physically fit inside the
    field box. Real corruption found in this database (audit 2026-08-11): OEP
    Form ``phone`` held 13 cells x 31.47pt = 409.11pt inside a 99.60pt box -- a
    4.1x overshoot, left behind when the box was moved and shrunk. Obeying such
    a pitch sprays characters across neighbouring fields and off the page edge.
    The field box is the trustworthy number: it is what the designer draws and
    what the operator positions over the pre-printed form, so an impossible
    pitch is clamped to one that exactly fills the box. A pitch that fits is
    returned untouched, so correctly-configured templates keep the exact
    spacing the designer chose.

    WHY THIS LIVES HERE (audit 2026-08-14)
    --------------------------------------
    This clamp existed ONLY in the HTML print path
    (``api/endpoints/documents.py::_fitted_cell_pitch``). The PDF engine read
    ``meta["cell_width"]`` raw and passed it straight to ``_draw_char_cells``,
    so for the exact corruption quoted above the two renderers of the SAME
    template disagreed by a factor of 4.1: the HTML output kept every digit
    inside its 99.6pt box at a 7.66pt pitch, while the PDF sprayed them
    409.11pt across the form. Nothing detected it, because the shipped
    templates happen to be clean -- the divergence only appears once a field is
    resized in the designer, which is ordinary use.

    Both paths now call this one function, so a bad ``cell_width`` produces the
    same (clamped) geometry everywhere. That is the same reasoning that put
    ``normalize_cell_value``/``cell_slots`` here in the first place: three
    renderers of one template have already drifted twice.
    """
    slots = grid_slot_span(cell_count, group_sizes)
    gaps = cell_gap * max(0, slots - 1)
    fitted = ((width - gaps) / slots) if slots else 0.0
    if stored_cell_width <= 0:
        return fitted, False
    # +1.0pt tolerance: a designer-chosen pitch that fills the box to the last
    # fraction of a point must not be "corrected" by a rounding artefact.
    if width > 0 and (stored_cell_width * slots + gaps) > width + 1.0:
        return fitted, True
    return stored_cell_width, False


def coerce_char_offset(raw: Any) -> int:
    """Validate operator-editable ``char_offset`` (multi-row comb support).

    WHY THIS EXISTS
    ---------------
    Several government forms print a single logical value across MORE THAN ONE
    row of boxes. On OEP form OEPE-RF-02 both field 6 (Name) and field 12
    (Address, in Pakistan) are two stacked rows of pre-printed cells: row 1
    holds the first N characters, row 2 continues from character N.

    Until now every char_cells field started at character 0, so a second row
    could only ever repeat the beginning of the value -- there was no way to
    express "continue here". The practical effect was that only row 1 got
    placed and any value longer than one row was silently truncated on the
    printed form (an address of 38 characters showed 13).

    ``char_offset`` is the number of leading characters to skip. Anything
    malformed or negative degrades to 0, matching the defensive contract of
    :func:`coerce_group_sizes`: bad template meta must never stop a document
    printing.
    """
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def _draw_char_cells(c: canvas.Canvas, text: str, x: float, y: float,
                     cell_count: int, cell_width: float, cell_gap: float = 0,
                     font_size: float = 11, font_bold: bool = True,
                     color: Color = black, draw_boxes: bool = False,
                     height: float = 0,
                     group_sizes: Optional[List[int]] = None,
                     char_offset: int = 0,
                     keep_spaces: bool = False,
                     pre_resolved: bool = False):
    """Render `text` one character per cell, left-aligned across `cell_count`
    cells starting at (x, y). Each cell is `cell_width` wide with `cell_gap`
    between them. The text baseline is placed inside each cell.

    ``group_sizes`` optionally splits the run into groups separated by one
    blank slot each -- see :func:`_cell_slots`.

    ``char_offset`` skips that many leading characters, which is how the second
    row of a two-row printed comb (OEP field 6 Name, field 12 Address) renders
    the continuation of one value -- see :func:`coerce_char_offset`.

    If `draw_boxes` is True we also stroke the cell borders (useful when the
    background doesn't already have them).
    """
    # `pre_resolved` means the caller already ran the value through
    # comb_capacity.resolve_comb_row(): normalised, capacity-checked against the
    # WHOLE multi-row comb, and sliced to exactly this row's characters. Doing
    # any of it again here would corrupt the result -- in particular
    # `.strip()` would eat the leading space of a continuation row (the blank
    # box that separates two words) and shift every later character one cell
    # left, and re-applying `char_offset` would slice an already-sliced row.
    if not pre_resolved:
        text = (text or "").strip()
        # Strip non-digit chars for numeric fields like CNIC; preserve otherwise
        # -- we let the caller decide by passing the value as-is.
        #
        # `keep_spaces` (format "text") exists because this second strip would
        # otherwise UNDO normalize_cell_value()'s space preservation: a name or
        # address comb needs a blank box between words, while an ID grid must not
        # spend a cell on punctuation. Dashes are still dropped either way -- a
        # printed name comb has no dash cells.
        text = text.replace("-", "")
        if not keep_spaces:
            text = text.replace(" ", "")
        # Multi-row combs: this row starts at character `char_offset` of the
        # value. Slicing AFTER separator-stripping keeps the offset counted in
        # the same units the cells hold (bare characters), so row 2 of a 13-cell
        # row 1 always uses char_offset=13 regardless of how the operator typed
        # dashes.
        if char_offset:
            text = text[char_offset:]

        # SILENT TRUNCATION MUST BE REPORTED.
        #
        # A comb can only ever show `cell_count` characters; anything beyond that
        # is dropped by the loop below without a word. On a government form that
        # is the worst failure mode there is -- the document looks complete and
        # nobody can tell that the last third of an address is missing.
        #
        # NOTE: this path is now only reached by callers that have NOT gone
        # through the capacity policy (internal/legacy callers). The policy path
        # reports a far better message, naming the dropped text and the reason,
        # from _render_field. This remains as a backstop so an un-migrated caller
        # can still never truncate in total silence.
        if len(text) > cell_count:
            log.warning(
                "char_cells overflow: %d characters do not fit %d cells -- %r is "
                "truncated to %r (increase char_offset rows or shorten the value)",
                len(text), cell_count, text, text[:cell_count],
            )
    else:
        text = text or ""

    fname = _font_name(font_bold, False, text)
    c.setFont(fname, font_size)
    c.setFillColor(color)

    slots = _cell_slots(cell_count, group_sizes)
    for i in range(cell_count):
        cell_x = x + slots[i] * (cell_width + cell_gap)
        if draw_boxes and height > 0:
            c.setStrokeColor(HexColor("#9ca3af"))
            c.setLineWidth(0.4)
            c.rect(cell_x, y - 2, cell_width, height, stroke=1, fill=0)
        if i < len(text):
            ch = text[i]
            # Center the digit inside the cell
            c.drawCentredString(cell_x + cell_width / 2, y, ch)


# ---------------------------------------------------------------------------
# Checkbox / tick
# ---------------------------------------------------------------------------
def _draw_checkbox(c: canvas.Canvas, x: float, y: float, size: float = 10,
                   checked: bool = True, color: Color = black):
    if checked:
        c.setStrokeColor(color)
        c.setLineWidth(1.4)
        # Draw a check mark inside the box (without drawing the box itself --
        # the background already has it)
        c.line(x + 1, y + size * 0.5, x + size * 0.4, y + 1)
        c.line(x + size * 0.4, y + 1, x + size - 1, y + size - 1)


# ---------------------------------------------------------------------------
# Barcode  --  professional, multi-symbology engine
#
# Supports: Code 128, Code 39, EAN-13.
# Honours the meta attributes captured in the Template Designer barcode panel:
#   - symbology / format        ("code128" | "code39" | "ean13")
#   - bar_height                (height of the bars, in points)
#   - bar_width                 (narrowest bar width / module width, in points)
#   - show_text / human_readable(draw the readable caption under the bars)
#   - caption_font_size         (font size of the readable caption)
#   - align                     ("left" | "center" | "right" within the field box)
#   - quiet_zone                (white margin around the symbol, in points)
#
# The barcode is rendered to a vector Drawing then placed so it never overflows
# the field box, and the human-readable caption is drawn cleanly & centred.
# ---------------------------------------------------------------------------
def _sanitize_barcode_value(value: str, symbology: str) -> str:
    """Clean the value so it is encodable by the chosen symbology."""
    value = (value or "").strip()
    if symbology == "code39":
        # Code39 only supports: 0-9 A-Z space - . $ / + %  →  upper-case the rest.
        value = value.upper()
        value = re.sub(r"[^0-9A-Z\-.\ $/+%]", "", value)
    elif symbology == "ean13":
        digits = re.sub(r"\D", "", value)
        # EAN-13 needs exactly 12 data digits (checksum auto-added). Pad/truncate.
        value = digits[:12].rjust(12, "0") if digits else ""
    # code128 can encode the full ASCII set, so leave it as-is.
    return value


def _draw_barcode(c: canvas.Canvas, value: str, x: float, y: float,
                  width: float, height: float, show_text: bool = True,
                  symbology: str = "code128", bar_width: Optional[float] = None,
                  bar_height: Optional[float] = None,
                  caption_font_size: float = 8.0,
                  align: str = "center", quiet_zone: Optional[float] = None,
                  color: Color = black):
    """Draw a clean, professional barcode that fits inside the field box.

    The symbol is auto-scaled so it is fully contained within ``width`` while
    keeping crisp module edges; the readable caption is centred under the bars.
    """
    symbology = (symbology or "code128").lower().replace("-", "").replace("_", "")
    if symbology in ("code-128", "c128"):
        symbology = "code128"
    if symbology in ("code-39", "c39", "3of9"):
        symbology = "code39"
    if symbology in ("qrcode", "qr-code", "qr_code"):
        symbology = "qr"

    raw = str(value or "")
    # QR codes accept arbitrary text (URLs, JSON, etc.) — never sanitize.
    value = raw if symbology == "qr" else _sanitize_barcode_value(raw, symbology)
    if not value:
        return

    # Geometry --------------------------------------------------------------
    box_w = float(width or 160)
    box_h = float(height or 32)
    bh = float(bar_height) if bar_height else max(box_h - (caption_font_size + 3 if show_text else 2), 14)
    qz = float(quiet_zone) if quiet_zone is not None else 4.0

    try:
        # QR code — a 2D matrix symbol scaled to fit the (square) box. -------
        if symbology == "qr":
            side = min(box_w, box_h)
            widget = _qr.QrCodeWidget(value)
            b = widget.getBounds()
            sym_w = b[2] - b[0]
            sym_h = b[3] - b[1]
            d = _QRDrawing(side, side, transform=[side / sym_w, 0, 0, side / sym_h,
                                                  -b[0] * side / sym_w, -b[1] * side / sym_h])
            d.add(widget)
            # Horizontal placement inside the box.
            if align == "center":
                qx = x + (box_w - side) / 2.0
            elif align == "right":
                qx = x + box_w - side
            else:
                qx = x
            renderPDF.draw(d, c, qx, y)
            return

        # Build the barcode as a vector Drawing so we can measure & scale it
        # precisely, then nudge it to fit the box without clipping.
        if symbology == "ean13":
            bc = eanbc.Ean13BarcodeWidget(value)
            bc.barHeight = bh
            if bar_width:
                bc.barWidth = float(bar_width)
            bc.humanReadable = bool(show_text)
            bc.fontSize = caption_font_size
            d = Drawing()
            d.add(bc)
            bounds = bc.getBounds()
            sym_w = bounds[2] - bounds[0]
            sym_h = bounds[3] - bounds[1]
            # scale to fit inside box width (minus quiet zones)
            avail = box_w - 2 * qz
            scale = min(1.0, avail / sym_w) if sym_w else 1.0
            tx = x + qz + (avail - sym_w * scale) / 2.0 if align == "center" else (
                 x + box_w - qz - sym_w * scale if align == "right" else x + qz)
            c.saveState()
            c.translate(tx - bounds[0] * scale, y - bounds[1] * scale)
            c.scale(scale, scale)
            renderPDF.draw(d, c, 0, 0)
            c.restoreState()
            return

        # Code128 / Code39 -- use the linear-barcode classes which draw directly.
        avail = box_w - 2 * qz
        if symbology == "code39":
            # First pass to measure width at requested module width
            bw = float(bar_width) if bar_width else 0.7
            bc = code39.Standard39(value, barHeight=bh, barWidth=bw,
                                   humanReadable=False, checksum=0,
                                   quiet=False)
        else:  # code128
            bw = float(bar_width) if bar_width else 0.8
            bc = code128.Code128(value, barHeight=bh, barWidth=bw,
                                 humanReadable=False, quiet=False)

        sym_w = bc.width
        # Auto-shrink the module width if the symbol is wider than the box.
        if sym_w > avail and sym_w > 0:
            bw = max(bw * (avail / sym_w), 0.33)  # 0.33pt ≈ min printable module
            if symbology == "code39":
                bc = code39.Standard39(value, barHeight=bh, barWidth=bw,
                                       humanReadable=False, checksum=0, quiet=False)
            else:
                bc = code128.Code128(value, barHeight=bh, barWidth=bw,
                                     humanReadable=False, quiet=False)
            sym_w = bc.width

        # Horizontal placement inside the box.
        if align == "center":
            bx = x + (box_w - sym_w) / 2.0
        elif align == "right":
            bx = x + box_w - qz - sym_w
        else:
            bx = x + qz

        # Caption sits below the bars; bars sit at the top of the box.
        cap_h = (caption_font_size + 2) if show_text else 0
        by = y + cap_h  # bottom of the bars
        c.setFillColor(color)
        bc.drawOn(c, bx, by)

        # Human-readable caption -- clean, centred, monospaced-ish.
        if show_text:
            c.setFillColor(color)
            try:
                c.setFont(_font_name(False, False), caption_font_size)
            except Exception:
                c.setFont("Helvetica", caption_font_size)
            c.drawCentredString(bx + sym_w / 2.0, y + 1, raw.strip() or value)
    except Exception as e:
        log.warning("barcode draw failed (%r, value=%r): %s", symbology, value, e)


def barcode_to_data_uri(value: str, symbology: str = "code128",
                        bar_height=None, bar_width=None, show_text: bool = True,
                        caption_font_size: float = 8.0,
                        box_w: float = 0.0, box_h: float = 0.0) -> str:
    """Render a barcode to a base64 PNG ``data:`` URI for HTML previews.

    Produces the *same* clean symbol used in the printed PDF so the on-screen
    print-preview matches the final output exactly. Returns "" on failure or
    when the value is empty.
    """
    import base64
    symbology = (symbology or "code128").lower().replace("-", "").replace("_", "")
    if symbology in ("code-128", "c128"):
        symbology = "code128"
    if symbology in ("code-39", "c39", "3of9"):
        symbology = "code39"
    if symbology in ("qrcode", "qr-code", "qr_code"):
        symbology = "qr"

    raw = str(value or "")

    # QR code preview — render via the pure-Python ``qrcode`` + PIL pipeline
    # (no cairo dependency) to a crisp PNG data URI. The printed PDF uses
    # reportlab's vector QR, but both encode the same value identically.
    if symbology == "qr":
        if not raw:
            return ""
        try:
            import qrcode as _qrlib
            qrobj = _qrlib.QRCode(border=1, box_size=8,
                                  error_correction=_qrlib.constants.ERROR_CORRECT_M)
            qrobj.add_data(raw)
            qrobj.make(fit=True)
            img = qrobj.make_image(fill_color="black", back_color="white")
            _buf = io.BytesIO()
            img.save(_buf, format="PNG")
            return "data:image/png;base64," + base64.b64encode(_buf.getvalue()).decode("ascii")
        except Exception as e:
            log.warning("qr preview failed: %s", e)
            return ""

    enc = _sanitize_barcode_value(raw, symbology)
    if not enc:
        return ""

    bh = float(bar_height) if bar_height not in (None, "", "auto") else 15.0
    bw = float(bar_width)  if bar_width  not in (None, "", "auto") else 0.8
    try:
        import barcode as _bc_lib
        from barcode.writer import SVGWriter as _SVGWriter

        # Map symbology to python-barcode name
        sym_map = {"code128": "code128", "code39": "code39", "ean13": "ean"}
        sym_name = sym_map.get(symbology, "code128")

        # -------------------------------------------------------------------
        # Render as crisp VECTOR SVG (not raster PNG). SVG scales to any size
        # with perfectly sharp thin bars and a clean human-readable caption —
        # matching a professional, scannable barcode. This eliminates the
        # blurry/pixelated raster look and stays razor-sharp at any zoom/DPI.
        # -------------------------------------------------------------------
        # -------------------------------------------------------------------
        # Proportioning: a real, scannable barcode is WIDE and SHORT (a long
        # strip of thin bars) — not a near-square block. python-barcode's
        # ``module_height`` is an absolute mm value, so if we pass the raw
        # ``bar_height`` (often ~26) the symbol comes out almost square and,
        # once dropped into a wide/short field box with ``object-fit:contain``,
        # collapses to a tiny illegible thumbnail with huge side margins.
        #
        # Fix: keep the bars thin (crisp) and clamp the module height so the
        # overall symbol keeps a natural wide aspect ratio (~4:1 .. 8:1). We
        # then inject an explicit viewBox + preserveAspectRatio so the browser
        # scales the vector to fill the field box width cleanly and sharply.
        # -------------------------------------------------------------------
        module_w = max(0.18, bw * 0.33)          # thin crisp bar unit (mm)
        font_sz = max(7, int(round(caption_font_size + 1)))
        quiet_zone = 1.5
        text_distance = 2.4

        def _render(mod_h: float) -> bytes:
            _buf = io.BytesIO()
            _bc = _bc_lib.get(sym_name, enc, writer=_SVGWriter())
            _bc.write(_buf, options={
                "write_text": bool(show_text),
                "module_width": module_w,
                "module_height": float(mod_h),
                "font_size": font_sz,
                "text_distance": text_distance,
                "quiet_zone": quiet_zone,
                "background": "white",
                "foreground": "black",
            })
            return _buf.getvalue()

        # Default comfortable strip height.
        module_h = 12.0

        # If we know the target field box, size the bar height so the WHOLE
        # symbol's aspect ratio (bars + caption) matches the box — this makes
        # object-fit:contain fill the box WIDTH (how a scanner reads a barcode)
        # instead of shrinking it to a tiny centred thumbnail.
        # Choose a bar height that keeps a natural, scannable barcode aspect.
        # A real Code128 symbol is a dense strip of MANY thin bars — we must
        # never widen the module (that destroys scannability). Instead we tune
        # only the bar HEIGHT so the whole symbol (bars + human-readable
        # caption) sits at a clean ~3:1..5:1 aspect. The field box then holds
        # it left-aligned via object-fit:contain; any extra box width is quiet
        # whitespace to the right, exactly like a printed government form.
        svg_bytes = _render(module_h)
        try:
            if box_w and box_h and box_w > 0 and box_h > 0:
                import re as _re0
                _m = _re0.search(r'width="([\d.]+)mm"', svg_bytes.decode("utf-8", "ignore"))
                if _m:
                    total_w = float(_m.group(1))           # symbol width (mm)
                    box_aspect = box_w / box_h
                    extras = (font_sz * 0.353) + text_distance + (quiet_zone * 2)
                    # Target the symbol aspect to the box aspect but clamp bar
                    # height to a sane, readable band (6..22 mm) so it is never
                    # a hairline nor a giant block.
                    target_total_h = total_w / max(box_aspect, 0.1)
                    module_h = min(22.0, max(6.0, target_total_h - extras))
                    svg_bytes = _render(module_h)
        except Exception:
            pass

        # Inject a viewBox derived from the SVG's own width/height (in mm) plus
        # preserveAspectRatio so it scales crisply to any container without
        # distortion. python-barcode emits <svg ... width="Xmm" height="Ymm">
        # but no viewBox, which makes object-fit/contain behave inconsistently.
        try:
            import re as _re
            svg_txt = svg_bytes.decode("utf-8", "ignore")
            m_w = _re.search(r'<svg[^>]*\bwidth="([\d.]+)mm"', svg_txt)
            m_h = _re.search(r'<svg[^>]*\bheight="([\d.]+)mm"', svg_txt)
            if m_w and m_h:
                vb_w, vb_h = float(m_w.group(1)), float(m_h.group(1))
                # CRITICAL: python-barcode emits child coordinates with an
                # explicit "mm" unit (e.g. x="33.444mm") but no viewBox. If we
                # add a *unitless* viewBox the child mm values no longer align
                # with the user-space grid, so only a few over-scaled bars
                # render. Fix by (a) stripping the "mm" suffix from every
                # child coordinate so they become plain user units, and
                # (b) adding a matching unitless viewBox + preserveAspectRatio.
                # The result scales razor-sharp to any container via object-fit.
                head, _, body = svg_txt.partition(">")  # split off the <svg ...> tag
                # Strip mm units only inside the body (child elements), keeping
                # the outer <svg> width/height in mm for a sensible intrinsic size.
                body = _re.sub(r'([xy]|width|height)="([\d.]+)mm"',
                               r'\1="\2"', body)
                # The caption <text> uses an absolute pt font-size which does
                # NOT scale with the (mm-based) viewBox, so after unit-stripping
                # it renders gigantic and overlaps the bars. Convert pt -> mm
                # user units (1pt = 0.352778mm) so the caption is a clean,
                # correctly-sized human-readable line beneath the bars.
                def _pt_to_userunit(mobj):
                    return f'font-size:{float(mobj.group(1)) * 0.352778:.3f}'
                body = _re.sub(r'font-size:([\d.]+)pt', _pt_to_userunit, body)
                # Ensure the outer tag carries a viewBox + preserveAspectRatio.
                if "viewBox" not in head:
                    head = head.replace(
                        "<svg",
                        f'<svg viewBox="0 0 {vb_w:.3f} {vb_h:.3f}" '
                        f'preserveAspectRatio="xMidYMid meet"',
                        1,
                    )
                svg_txt = head + ">" + body
                svg_bytes = svg_txt.encode("utf-8")
        except Exception:
            pass

        # SVGWriter emits a full XML document; embed it directly as a data URI.
        return "data:image/svg+xml;base64," + base64.b64encode(svg_bytes).decode("ascii")
    except Exception as e:
        log.warning("barcode_to_data_uri failed (%r): %s", symbology, e)
        # Fallback to raster PNG so a barcode still appears if SVG generation
        # is unavailable for any reason.
        try:
            import barcode as _bc_lib2
            from barcode.writer import ImageWriter as _ImageWriter
            sym_map = {"code128": "code128", "code39": "code39", "ean13": "ean"}
            sym_name = sym_map.get(symbology, "code128")
            buf = io.BytesIO()
            bc = _bc_lib2.get(sym_name, enc, writer=_ImageWriter())
            bc.write(buf, options={
                "write_text": bool(show_text),
                "module_width": max(0.2, bw * 0.4),
                "module_height": float(bh),
                "font_size": max(6, int(caption_font_size * 1.4)),
                "text_distance": 3.0,
                "quiet_zone": 3.0,
                "dpi": 300,
            })
            return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception:
            return ""


@lru_cache(maxsize=512)
def _cached_barcode_svg(value: str, symbology: str, bar_height: float,
                        bar_width: float, show_text: bool,
                        caption_font_size: float, box_w: float,
                        box_h: float) -> str:
    """LRU wrapper around barcode_to_data_uri for the overlay live-preview.

    Placed *after* barcode_to_data_uri to avoid a forward-reference NameError.
    Key is the full set of rendering parameters — any change (new candidate
    value, different field size) produces a fresh render while identical calls
    are served from the in-process cache.

    Cache size 512 comfortably covers a portal with hundreds of candidates
    each with a handful of barcode field types before the LRU evicts.
    """
    return barcode_to_data_uri(
        value, symbology=symbology, bar_height=bar_height or None,
        bar_width=bar_width or None, show_text=show_text,
        caption_font_size=caption_font_size, box_w=box_w, box_h=box_h,
    )


# ---------------------------------------------------------------------------
# Trade table -- auto-build the Visa Category / Qty / Assigned / Available rows
# ---------------------------------------------------------------------------
#: Default column spec for trade_table -- used when meta.columns is absent.
#: Each entry: (header_label, data_key, weight, align)
#:   weight  = relative column-width share (sum need not equal 1)
#:   data_key = JobCategory attribute, or "_assigned"/"_available" for derived counts
_TRADE_TABLE_DEFAULT_COLUMNS = [
    ("Sr#",         "_sr",            0.06, "center"),
    ("Trade / Visa Category", "trade", 0.36, "left"),
    ("Qty",         "quantity",       0.08, "center"),
    ("Assigned",    "_assigned",      0.10, "center"),
    ("Available",   "_available",     0.10, "center"),
    ("Salary",      "salary",         0.15, "right"),
    ("Contract",    "contract_period", 0.15, "center"),
]


def _draw_trade_table(c: canvas.Canvas, demand_id: int, x: float, y: float,
                      width: float, row_height: float, font_size: float, db,
                      meta: Optional[Dict] = None):
    """Render the trades table for a demand starting at (x, y) and growing
    downward (PDF coords -- origin bottom-left, so successive rows decrease y).

    The table is **fully configurable via the field's meta dict** -- the
    designer property panel writes these keys:

        meta.columns       -- list of {key, label, weight, align} OR a CSV of
                             column keys (e.g. "trade,quantity,salary"). When
                             omitted we render the full demo-OEP column set.
        meta.show_header   -- bool (default True). Draw a bold header row.
        meta.row_height    -- float pt (also accepted as the field's `height`).
        meta.padding_x     -- horizontal cell padding in pt (default 3).
        meta.header_fill   -- header background hex (default "#e5e7eb").
        meta.border        -- draw cell borders? (default True).
        meta.zebra         -- alternate-row background (default False).
        meta.max_rows      -- clamp row count (default 0 = no limit).

    The function is no-op when there's no demand_id or db handle so it stays
    safe in unit tests / preview-without-data scenarios.
    """
    meta = meta or {}
    if not demand_id or not db:
        # Still render the HEADER even with no demand_id so the user sees the
        # table shape in the designer preview -- important for visual fidelity
        # between designer and print.
        rows: List = []
    else:
        try:
            from sqlalchemy import func as sqlfunc

            from app.models import CandidateAssignment, JobCategory
            rows = db.query(
                JobCategory,
                sqlfunc.count(CandidateAssignment.id).label("assigned"),
            ).outerjoin(
                CandidateAssignment,
                CandidateAssignment.job_category_id == JobCategory.id,
            ).filter(JobCategory.demand_id == demand_id).group_by(JobCategory.id).all()
        except Exception as e:
            log.warning("trade_table query failed: %s", e)
            rows = []

    # ------- Resolve column spec -------
    raw_cols = meta.get("columns")
    columns: List[tuple] = []
    if isinstance(raw_cols, list) and raw_cols:
        # Full spec list
        for spec in raw_cols:
            if isinstance(spec, dict):
                columns.append((
                    str(spec.get("label") or spec.get("key") or ""),
                    str(spec.get("key") or ""),
                    float(spec.get("weight") or 1.0),
                    str(spec.get("align") or "left").lower(),
                ))
    elif isinstance(raw_cols, str) and raw_cols.strip():
        # CSV of keys -- derive labels from default set
        keys = [k.strip() for k in raw_cols.split(",") if k.strip()]
        defaults = {k: (lbl, w, al) for (lbl, k, w, al) in _TRADE_TABLE_DEFAULT_COLUMNS}
        for k in keys:
            lbl, w, al = defaults.get(k, (k.replace("_", " ").title(), 0.15, "left"))
            columns.append((lbl, k, w, al))
    if not columns:
        columns = list(_TRADE_TABLE_DEFAULT_COLUMNS)

    show_header = bool(meta.get("show_header", True))
    pad_x = float(meta.get("padding_x", 3))
    header_fill = _hex_to_color(str(meta.get("header_fill", "#e5e7eb")))
    border = bool(meta.get("border", True))
    zebra = bool(meta.get("zebra", False))
    zebra_fill = _hex_to_color(str(meta.get("zebra_fill", "#f8fafc")))
    max_rows = int(meta.get("max_rows") or 0)

    # Normalise weights and compute column widths in pt
    total_weight = sum(w for (_, _, w, _) in columns) or 1.0
    col_widths = [width * (w / total_weight) for (_, _, w, _) in columns]

    if max_rows:
        rows = rows[:max_rows]

    # ------- Draw -------
    fname_bold = _font_name(True, False)
    fname_body = _font_name(False, False)

    cur_y = y  # baseline of the current row (PDF coords)

    def _draw_row_bg(top_y: float, fill_color):
        """Draw a filled rectangle for one row at baseline top_y."""
        c.saveState()
        c.setFillColor(fill_color)
        c.setStrokeColor(fill_color)
        # row spans [top_y - row_height + descender .. top_y + ascender]
        c.rect(x, top_y - (row_height - font_size), width, row_height,
               stroke=0, fill=1)
        c.restoreState()

    def _draw_row_cells(values: List[str], font_name: str, baseline_y: float):
        cx = x
        c.setFont(font_name, font_size)
        c.setFillColor(black)
        for (val, w, (_, _, _, align)) in zip(values, col_widths, columns, strict=False):
            text = str(val or "")
            # Truncate long text to fit the cell width approximately
            max_chars = max(4, int((w - 2 * pad_x) / (font_size * 0.55)))
            if len(text) > max_chars:
                text = text[:max_chars - 1] + "…"
            if align == "center":
                c.drawCentredString(cx + w / 2, baseline_y, text)
            elif align == "right":
                c.drawRightString(cx + w - pad_x, baseline_y, text)
            else:
                c.drawString(cx + pad_x, baseline_y, text)
            cx += w

    def _draw_row_borders(top_y: float):
        if not border:
            return
        c.saveState()
        c.setStrokeColor(HexColor("#94a3b8"))
        c.setLineWidth(0.4)
        # outer + vertical separators
        row_top_css = top_y - (row_height - font_size)
        c.rect(x, row_top_css, width, row_height, stroke=1, fill=0)
        cx = x
        for w in col_widths[:-1]:
            cx += w
            c.line(cx, row_top_css, cx, row_top_css + row_height)
        c.restoreState()

    # Header row
    if show_header:
        _draw_row_bg(cur_y, header_fill)
        headers = [lbl for (lbl, _, _, _) in columns]
        _draw_row_cells(headers, fname_bold, cur_y)
        _draw_row_borders(cur_y)
        cur_y -= row_height

    # Data rows
    for idx, (jc, assigned) in enumerate(rows, start=1):
        qty = int(jc.quantity or 0)
        assigned = int(assigned or 0)
        derived = {
            "_sr":         str(idx),
            "_assigned":   str(assigned),
            "_available":  str(max(0, qty - assigned)),
        }
        values = []
        for (_, key, _, _) in columns:
            if key in derived:
                values.append(derived[key])
            else:
                v = getattr(jc, key, "")
                # Format common types nicely
                if key == "salary" and v:
                    try:
                        v = f"{float(v):,.0f}"
                    except (TypeError, ValueError):
                        pass
                values.append("" if v is None else str(v))

        if zebra and (idx % 2 == 0):
            _draw_row_bg(cur_y, zebra_fill)
        _draw_row_cells(values, fname_body, cur_y)
        _draw_row_borders(cur_y)
        cur_y -= row_height


# ---------------------------------------------------------------------------
# Auto-grid fallback (unchanged from v1 but using new font picker)
# ---------------------------------------------------------------------------
_AUTO_FIELDS = {
    "candidate": [
        ("Full Name", "full_name"),
        ("Father Name", "father_name"),
        ("CNIC", "cnic"),
        ("Passport No", "passport_no"),
        ("Passport Expiry", "passport_expiry_date"),
        ("Date of Birth", "date_of_birth"),
        ("Nationality", "nationality"),
        ("Profession", "profession"),
        ("Address", "address"),
        ("Phone", "phone"),
        ("Next of Kin", "next_of_kin_name"),
        ("Relation", "next_of_kin_relation"),
    ],
    "demand": [
        ("File Number", "file_number"),
        ("Permission No", "permission_no"),
        ("Sponsor Name", "sponsor_name"),
        ("Sponsor Address", "sponsor_address"),
        ("Sponsor Phone", "sponsor_phone"),
        ("Visa Number", "visa_number"),
        ("Country", "country"),
        ("Embassy", "embassy"),
        ("Receiving Date", "receiving_date"),
        ("Visa Issue Date", "visa_issue_date"),
    ],
    "client": [
        ("Company Name", "company_name"),
        ("Owner", "owner_name"),
        ("Country", "country"),
        ("City", "city"),
        ("Phone", "phone"),
        ("Email", "email"),
        ("Address", "address"),
        ("OEP License", "oep_license_number"),
    ],
    # REMOVED 2026-07-30: the "agent" header field list, along with the
    # Sub-Agents feature itself (section 4.11 of PROJECT_DOCUMENTATION.md).
}


def _draw_record_header(c, template, record, w, h):
    band_h = 56
    c.saveState()
    c.setFillColor(HexColor("#1e40af"))
    c.rect(0, h - band_h, w, band_h, stroke=0, fill=1)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(20, h - 24, template.name or "Document")
    c.setFont("Helvetica", 10)
    subtitle = ""
    if record is not None:
        subtitle = (
            getattr(record, "full_name", None)
            or getattr(record, "file_number", None)
            or getattr(record, "company_name", None)
            or getattr(record, "name", None)
            or f"#{getattr(record, 'id', '')}"
        )
        if template.data_source == "demand" and subtitle and str(subtitle).isdigit():
            subtitle = f"DTC/786/{subtitle}"
    c.drawString(20, h - 42, f"{(template.data_source or 'record').title()}: {subtitle}")
    c.drawRightString(w - 20, h - 24, datetime.now().strftime("%d-%m-%Y %H:%M"))
    c.restoreState()


def _draw_auto_grid(c, template, record, w, h):
    if record is None:
        c.setFont("Helvetica-Oblique", 11)
        c.setFillColor(HexColor("#6b7280"))
        c.drawCentredString(w / 2, h / 2, "(No record selected -- preview)")
        return
    auto = _AUTO_FIELDS.get(template.data_source or "", [])
    if not auto:
        return
    start_y = h - 110
    row_h = 22
    col_w = (w - 80) / 2
    c.setFont("Helvetica", 10)
    c.setFillColor(black)
    for idx, (label, key) in enumerate(auto):
        col = idx % 2
        row = idx // 2
        x = 40 + col * col_w
        y = start_y - row * row_h
        if y < 60:
            break
        value = _resolve_record_value(record, key) or "--"
        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(HexColor("#374151"))
        c.drawString(x, y, f"{label}:")
        # Arabic-aware
        if _has_arabic(value):
            c.setFont(_font_name(False, False, value), 10)
            shaped = shape_arabic(value)
            c.setFillColor(black)
            c.drawString(x + 95, y, shaped)
        else:
            c.setFont(_font_name(False, False, value), 10)
            c.setFillColor(black)
            if len(value) > 38:
                value = value[:35] + "…"
            c.drawString(x + 95, y, value)


def _draw_footer(c, w, h):
    c.saveState()
    c.setFont("Helvetica-Oblique", 8)
    c.setFillColor(HexColor("#9ca3af"))
    c.drawCentredString(w / 2, 18,
        f"Generated by Dogar Trading Corporation Portal · "
        f"{datetime.now().strftime('%d-%m-%Y %H:%M')}")
    c.restoreState()


def _draw_content_notes(c, notes: str, page_width: float, page_height: float):
    """Render the free-form notes from the designer's Content tab at the
    bottom of the page, just above the footer. Audit Fix 3 -- previously the
    notes were saved (via the description meta-fence) but never appeared on
    the printed/PDF output.

    Layout:
        • full page width minus 30pt left/right margin
        • body starts ~50pt above the footer baseline (y=18)
        • automatic line-wrap at ~92 chars per line, max 8 lines
        • italic Helvetica 8pt, soft grey colour
    """
    if not notes:
        return
    text = str(notes).strip()
    if not text:
        return
    c.saveState()
    c.setFont("Helvetica-Oblique", 8)
    c.setFillColor(HexColor("#475569"))
    margin = 30
    max_chars = max(40, int((page_width - 2 * margin) / 4.4))
    # Simple word-wrap
    lines: List[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line:
            lines.append("")
            continue
        while len(line) > max_chars:
            # Break at the last space within max_chars, else hard-cut.
            cut = line.rfind(" ", 0, max_chars)
            if cut <= 0:
                cut = max_chars
            lines.append(line[:cut])
            line = line[cut:].lstrip()
        lines.append(line)
    lines = lines[:8]
    # Stack lines upward from y=50 (baseline of last line)
    base_y = 50
    line_h = 10
    for i, ln in enumerate(reversed(lines)):
        c.drawString(margin, base_y + i * line_h, ln)
    c.restoreState()


# ---------------------------------------------------------------------------
# Field rendering dispatcher
# ---------------------------------------------------------------------------
_MERGE_TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


def _resolve_merge_text(text: str, resolved_data: Optional[Dict] = None,
                        record: Any = None) -> str:
    """Replace ``{{token}}`` placeholders with resolved values.

    Used for the barcode-content box (and any future free-text merge field),
    mirroring the legacy portal where the barcode content is e.g. ``{{visa_number}}``
    or a combined value like ``E{{e_number}}`` or ``FILE-{{file_number}}``.
    Each token is looked up first in the pre-resolved data map, then on the
    record itself; unknown tokens collapse to an empty string.
    """
    if not text:
        return ""
    resolved_data = resolved_data or {}

    def _sub(m):
        key = m.group(1)
        if key in resolved_data and resolved_data[key] not in (None, ""):
            return str(resolved_data[key])
        val = _resolve_record_value(record, key) if record is not None else ""
        return str(val or "")

    return _MERGE_TOKEN_RE.sub(_sub, text).strip()


def _render_field(c: canvas.Canvas, field: DocumentField, value: str,
                  record: Any, db, page_w: float, page_h: float,
                  demand_id: Optional[int] = None,
                  resolved_data: Optional[Dict] = None):
    """Render a single coordinate-mapped field according to its type."""
    ft = (field.field_type or "text").lower()
    x = float(field.x or 0)
    y = float(field.y or 0)
    w = float(field.width or 200)
    font_size = float(field.font_size or 11)
    # COORDINATE PARITY: identical height fallbacks to the print view
    # (documents.py) and the designer engine (engine.ts):
    #   text-like  -> max(fs*1.25, 14)   (documents.py L1243)
    #   barcode    -> 32                 (documents.py L1267/1276)
    # The old flat `or 20` fallback drifted the text baseline up to 6pt vs
    # print whenever height was NULL/0.
    if field.height:
        h = float(field.height)
    elif ft == "barcode":
        h = 32.0
    else:
        h = max(font_size * 1.25, 14.0)
    bold = bool(field.font_bold)
    italic = bool(field.font_italic)
    color = _hex_to_color(field.color or "#000000")

    # OFF-PAGE FIELDS MUST NOT FAIL SILENTLY.
    #
    # A field whose box lies outside the page box is simply not visible in the
    # output: reportlab clips it, no exception is raised and no log line is
    # written, so the document prints with a value missing and looks merely
    # "incomplete" rather than broken. That is the worst possible failure mode on
    # a government form -- nobody can tell whether the value was absent from the
    # record or silently dropped by the renderer.
    #
    # This shipped: 'Allied Bank Deposit Form' carries customer_oep_license at
    # y=1004.1 on a 792pt page -- 212pt above the top edge, invisible on all 15
    # documents generated from that template. The coordinate fits A3 (1191pt),
    # so the page was almost certainly resized down to Letter long after the
    # field was authored, orphaning it. Nothing reported it.
    #
    # Deliberately a warning and not a clamp: moving the value to the nearest
    # in-page position would put it somewhere arbitrary on an official form,
    # which is worse than leaving it absent. The renderer's job is to say so.
    # scripts/audit_template_geometry.py reports the same thing ahead of render.
    #
    # Only an off-page ORIGIN is logged. `w` is a wrap width, not the glyph
    # extent, so a box that merely crosses an edge still prints its value and
    # would flood the log with false alarms (template 1's `cnic` is x=388.8 w=260
    # on a 612pt page and renders perfectly).
    if page_w and page_h and (x < 0 or y < 0 or x > page_w or y > page_h):
        log.warning(
            "off-page field: template_id=%s field=%r origin=(x=%.1f y=%.1f) page=(%.0fx%.0f) "
            "-- outside the page box, so this value will NOT appear in the document",
            getattr(field, "template_id", "?"), field.field_key or field.label,
            x, y, page_w, page_h,
        )

    meta = field.meta or {}
    if isinstance(meta, str):
        try:
            import json
            meta = json.loads(meta)
        except Exception:
            meta = {}

    # COORDINATE FIX (Problem 6 -- print/PDF coordinate drift):
    # The React designer canvas (designer/engine.ts + FieldMarker.tsx — the
    # single frontend coordinate engine) renders each
    # field as a BOX whose top edge sits at (page_h - y - h) and whose text
    # flows from the TOP of that box (line-height 1.05, no flex). reportlab's
    # drawString(x, y, ...) however puts the text BASELINE at y (the box
    # BOTTOM), so whenever h != font_size the PDF text drifts (h-font_size)pt
    # below where the user positioned it. We mirror the designer exactly by
    # drawing the baseline near the TOP of the box: baseline = y + h - ascent,
    # with ascent ≈ font_size (Arial cap+ascent). This makes designer == print
    # HTML == PDF, pixel-for-pixel.
    text_baseline_y = y + max(h - font_size, 0.0)

    def _draw_styled_text(text: str, *, prefer_arabic: bool = False) -> None:
        """Render designer typography metadata consistently in generated PDFs."""
        custom_font = str(meta.get("font_name") or "")
        font_name = _font_name(bold, italic, text, prefer_arabic=prefer_arabic, custom_font_name=custom_font)
        padding = max(0.0, float(meta.get("padding") or 0))
        char_space = float(meta.get("letter_spacing") or 0)
        opacity = min(100.0, max(0.0, float(meta.get("opacity", 100) or 100))) / 100.0
        multiline = bool(meta.get("multiline"))
        line_setting = float(meta.get("line_height") or 0)
        leading = (font_size * line_setting if 0 < line_setting <= 4 else line_setting) or (font_size * 1.2)
        draw_x = x + padding
        usable_w = max(1.0, w - 2 * padding)
        c.saveState()
        if hasattr(c, "setFillAlpha"):
            c.setFillAlpha(opacity)
        if meta.get("bg_enabled"):
            c.setFillColor(_hex_to_color(str(meta.get("bg_color") or "#ffffff")))
            c.rect(x, y, w, h, stroke=0, fill=1)
        c.setFillColor(color)
        c.setFont(font_name, font_size)

        lines = str(text or "").splitlines() or [""]
        if multiline:
            wrapped = []
            for source_line in lines:
                words = source_line.split(" ")
                current = ""
                for word in words:
                    candidate = word if not current else f"{current} {word}"
                    width = pdfmetrics.stringWidth(candidate, font_name, font_size) + max(0, len(candidate) - 1) * char_space
                    if current and width > usable_w:
                        wrapped.append(current)
                        current = word
                    else:
                        current = candidate
                wrapped.append(current)
            lines = wrapped
        else:
            lines = [lines[0]]

        baseline = text_baseline_y - padding
        for line in lines:
            if baseline < y - leading:
                break
            line_width = pdfmetrics.stringWidth(line, font_name, font_size) + max(0, len(line) - 1) * char_space
            if field.align == "center":
                line_x = draw_x + max(0, (usable_w - line_width) / 2)
            elif field.align == "right":
                line_x = draw_x + max(0, usable_w - line_width)
            else:
                line_x = draw_x
            text_obj = c.beginText(line_x, baseline)
            text_obj.setFont(font_name, font_size)
            text_obj.setFillColor(color)
            if char_space:
                text_obj.setCharSpace(char_space)
            text_obj.textOut(line)
            c.drawText(text_obj)
            if meta.get("font_underline"):
                c.setStrokeColor(color)
                c.setLineWidth(max(0.4, font_size / 18))
                c.line(line_x, baseline - font_size * 0.12, line_x + line_width, baseline - font_size * 0.12)
            if meta.get("font_strikethrough"):
                c.setStrokeColor(color)
                c.setLineWidth(max(0.4, font_size / 18))
                c.line(line_x, baseline + font_size * 0.3, line_x + line_width, baseline + font_size * 0.3)
            baseline -= leading
        c.restoreState()

    # --- static text ---
    if ft == "static":
        text = field.static_value or ""
        if _has_arabic(text):
            text = shape_arabic(text)
        _draw_styled_text(text)
        return

    # --- checkbox ---
    if ft == "checkbox":
        truthy = bool(value) and str(value).lower() not in ("", "0", "false", "no", "none")
        _draw_checkbox(c, x, y, size=max(h, 8), checked=truthy, color=color)
        return

    # --- photo ---
    if ft == "photo":
        photo_path = _resolve_photo_path(record, field.field_key or "photo")
        _draw_photo(c, photo_path or "", x, y, w, h,
                    fit=meta.get("fit", "cover"),
                    border=meta.get("border", True))
        return

    # --- char cells (CNIC, passport, phone digit grids) ---
    if ft == "char_cells":
        # ADDRESS CAPACITY POLICY (see app/services/comb_capacity.py).
        #
        # A comb row used to slice the value itself and truncate at its own
        # cell_count, with no idea that a SECOND row (char_offset=13) existed --
        # so a 55-character address was cut mid-word at 26 characters and the
        # district silently vanished. 83.4% of stored addresses are over
        # capacity, so this was the normal case, not an edge case.
        #
        # resolve_comb_row() makes the decision ONCE for the whole value, using
        # the real multi-row capacity discovered from the sibling fields, and
        # hands back exactly the characters this row prints. The HTML print path
        # and the designer preview call the same function, which is the only
        # reason the three renderers agree.
        _sibling_fields = getattr(getattr(field, "template", None), "fields", None)
        _row_text, _fit = resolve_comb_row(
            value, meta,
            sibling_fields=_sibling_fields,
            field_key=field.field_key or "",
            page=int(field.page or 1),
        )
        if _fit.review_required:
            # Not a shrink-to-fit: the box pitch is fixed by the pre-printed
            # form, so there is no legitimate way to render more characters. The
            # operator has to shorten the value (or the template needs another
            # row) and must be told which -- with the reason, not just a count.
            log.warning("comb capacity (%s): %s", field.field_key or "?", _fit.reason)

        cell_count = int(meta.get("cell_count") or len(str(value or "")) or 13)
        cell_gap = float(meta.get("cell_gap") or 0)
        draw_boxes = bool(meta.get("draw_boxes", False))
        # Optional grouping, e.g. [5, 7, 1] for a printed CNIC grid. Coerced
        # defensively: template meta is operator-editable JSON, and a bad value
        # here must degrade to the plain even run rather than break rendering.
        group_sizes = coerce_group_sizes(meta.get("group_sizes"), cell_count)

        # PITCH CLAMP (audit 2026-08-14).
        #
        # This used to be `float(meta["cell_width"] or w / cell_count)` -- the
        # stored pitch, obeyed unconditionally. The HTML print path has clamped
        # an impossible pitch to the field box since 2026-08-11, but this path
        # never did, so the two renderers of one template disagreed by up to
        # 4.1x on the real corruption found in this database (OEP `phone`:
        # 13 x 31.47pt = 409.11pt of cells inside a 99.60pt box). HTML kept the
        # digits in the box; the PDF sprayed them across the form.
        #
        # The shipped templates are clean, which is exactly why nothing caught
        # it: the divergence only appears once someone resizes a field in the
        # designer -- ordinary use, not an edge case.
        cell_width, _was_clamped = fitted_cell_pitch(
            w, cell_count, cell_gap,
            float(meta.get("cell_width") or 0), meta.get("group_sizes"),
        )
        if cell_width <= 0:
            # No usable box width AND no stored pitch: fall back to the old
            # even division so a misconfigured field still prints something.
            cell_width = (w / cell_count) if (w > 0 and cell_count) else 18.0
        if _was_clamped:
            log.warning(
                "char_cells pitch (%s): stored cell_width %.2fpt x %d slots does "
                "not fit the %.2fpt field box; clamped to %.2fpt so the value "
                "stays inside its printed cells. Fix the field in the Template "
                "Designer -- the box, not the render, is the trustworthy number.",
                field.field_key or "?", float(meta.get("cell_width") or 0),
                grid_slot_span(cell_count, meta.get("group_sizes")), w, cell_width,
            )

        # Optional format pre-processing for cell values:
        #   "ddmmyyyy" -- convert "13-Feb-2015" / "13-02-2015" → "13022015"
        #   "digits"   -- strip all non-digit chars (e.g. CNIC "12345-6789012-3" → "1234567890123")
        #   "alnum"    -- strip all non-alphanumeric chars
        # `_row_text` is already normalized, capacity-checked and sliced for
        # THIS row by resolve_comb_row() above, so it is passed verbatim:
        # pre_resolved=True suppresses the legacy strip/space/offset handling.
        # That matters -- a continuation row can legitimately BEGIN with a space
        # (the blank box between two words), and the old `.strip()` would have
        # eaten it and shifted every remaining character one cell to the left.
        _draw_char_cells(c, _row_text, x, y,
                         cell_count=cell_count,
                         cell_width=cell_width,
                         cell_gap=cell_gap,
                         font_size=font_size,
                         font_bold=bold,
                         color=color,
                         draw_boxes=draw_boxes,
                         height=h,
                         group_sizes=group_sizes,
                         char_offset=0,
                         keep_spaces=True,
                         pre_resolved=True)
        return

    # --- barcode ---
    if ft == "barcode":
        # Resolve the encoded value. Priority order (matches the legacy portal):
        #   1) meta.barcode_content  -- supports {{merge}} tokens + fixed prefixes
        #   2) field.static_value    -- a literal value typed in the designer
        #   3) the field_key's resolved value (legacy behaviour)
        content_spec = (meta.get("barcode_content")
                        or meta.get("content")
                        or field.static_value
                        or "")
        if content_spec:
            barcode_value = _resolve_merge_text(content_spec, resolved_data, record)
        else:
            barcode_value = str(value or "")

        symbology = (meta.get("symbology") or meta.get("format") or "code128")
        bar_w = meta.get("bar_width")
        bar_h = meta.get("bar_height")
        cap_fs = float(meta.get("caption_font_size")
                       or meta.get("caption_font") or 8.0)
        show_txt = meta.get("show_text", meta.get("human_readable", True))
        bc_align = meta.get("align") or field.align or "center"
        _draw_barcode(
            c, barcode_value, x, y, w, h,
            show_text=bool(show_txt),
            symbology=symbology,
            bar_width=float(bar_w) if bar_w not in (None, "", "auto") else None,
            bar_height=float(bar_h) if bar_h not in (None, "", "auto") else None,
            caption_font_size=cap_fs,
            align=bc_align,
            quiet_zone=float(meta["quiet_zone"]) if meta.get("quiet_zone") not in (None, "") else None,
            color=color,
        )
        return

    # --- trade table ---
    if ft == "trade_table":
        row_h = float(meta.get("row_height") or h or 18)
        _draw_trade_table(c, demand_id, x, y, w, row_h, font_size, db, meta=meta)
        return

    # --- arabic explicit ---
    if ft == "arabic":
        text = shape_arabic(str(value or ""))
        _draw_styled_text(text, prefer_arabic=True)
        return

    # --- text/date (default) ---
    text = str(value or "")
    # Optional date reformatting via meta.format so boxed date rows
    # (e.g. the OEP "dd-mm-yyyy" boxes) get clean digit-with-dash output
    # instead of "07-May-1992".
    fmt = (meta.get("format") or "").lower() if isinstance(meta, dict) else ""
    if fmt in ("ddmmyyyy", "date_dashes", "dd-mm-yyyy", "dd/mm/yyyy") and text:
        digits = _format_date_ddmmyyyy(text)
        if len(digits) == 8:
            if fmt == "ddmmyyyy":
                text = digits
            elif fmt == "dd/mm/yyyy":
                text = f"{digits[0:2]}/{digits[2:4]}/{digits[4:8]}"
            else:  # date_dashes / dd-mm-yyyy
                text = f"{digits[0:2]}-{digits[2:4]}-{digits[4:8]}"
    if _has_arabic(text):
        text = shape_arabic(text)
    _draw_styled_text(text)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def generate_pdf(
    db,
    template: DocumentTemplate,
    record_id: Optional[int] = None,
    output_path: Optional[str] = None,
) -> str:
    """Generate a filled PDF for the given template + record."""
    record = _load_record(db, template.data_source, record_id)

    from app.services.overlay_engine import resolve_all_related_data_for_record
    resolved_data = {}
    demand_id = None
    if db and record_id:
        resolved_data = resolve_all_related_data_for_record(
            db, template.data_source, record_id
        )
        # Determine demand_id for trade_table support
        if template.data_source == "demand":
            demand_id = record_id
        elif template.data_source == "candidate":
            try:
                from app.models import CandidateAssignment, JobCategory
                a = (db.query(CandidateAssignment)
                       .filter(CandidateAssignment.candidate_id == record_id)
                       .order_by(CandidateAssignment.id.desc())
                       .first())
                if a:
                    jc = db.query(JobCategory).filter(JobCategory.id == a.job_category_id).first()
                    if jc:
                        demand_id = jc.demand_id
            except (AttributeError, Exception) as exc:  # noqa: BLE001 -- SQLAlchemy may raise various
                # Demand resolution is best-effort -- the renderer must still
                # produce SOMETHING even if the candidate has no assignment.
                import logging
                logging.getLogger("dtc.pdf_engine").warning(
                    "Could not resolve demand from candidate assignment: %s", exc
                )

    # COORDINATE MISMATCH FIX (2026-08-18). This used to read
    #     page_width  = float(template.page_width  or A4[0])   # 595.2756
    #     page_height = float(template.page_height or A4[1])   # 841.8898
    # i.e. reportlab's EXACT A4, while the DocumentTemplate columns default to
    # the ROUNDED 595 x 842 and every authored coordinate in the designer was
    # placed against that rounded page. So one template rendered on two
    # different pages depending purely on whether those columns had ever been
    # written, and a NULL height produced a page 0.1102 pt shorter than the one
    # the operator designed against -- displacing every field on it, because
    # PDF y is measured from the bottom edge.
    #
    # resolve_page_size() is the single authority shared with the print view
    # and asserted against the designer's PAGE_SIZES table by
    # tests/test_geometry_contract.py.
    page_width, page_height = resolve_page_size(
        template.page_width, template.page_height,
    )

    if output_path is None:
        out_dir = os.path.join(settings.UPLOAD_DIR, "generated_pdfs")
        os.makedirs(out_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = "".join(ch for ch in (template.name or "doc")
                            if ch.isalnum() or ch in "_-")[:50] or "doc"
        output_path = os.path.join(
            out_dir, f"{safe_name}_{record_id or 'blank'}_{stamp}.pdf"
        )

    c = canvas.Canvas(output_path, pagesize=(page_width, page_height))
    c.setTitle(template.name or "Document")
    c.setAuthor("Dogar Trading Corporation Portal")
    c.setSubject(f"{template.data_source or 'record'} #{record_id or 'blank'}")

    bg_path = _resolve_background_path(template)

    # Parse designer meta (Content-tab notes etc.) from the description
    # meta-fence so they get rendered on the printed/PDF page. (Audit Fix 3)
    from app.services.overlay_engine import parse_designer_meta
    _designer_meta = parse_designer_meta(getattr(template, "description", "") or "")
    _content_notes = (_designer_meta.get("notes") or "").strip()
    _bg_fit = str(_designer_meta.get("bg_fit") or "fill").strip().lower()
    if _bg_fit not in ("fill", "contain", "actual"):
        _bg_fit = "fill"

    # Per-page backgrounds: a single template can render a different scanned
    # form per page (e.g. OEP form front on page 1, back on page 2 → ONE PDF).
    # Configured via designer meta key ``page_backgrounds`` = {"2": "oep_form_p2.jpg"}.
    _page_bg_paths: Dict[int, Optional[str]] = {}
    _pbg_meta = _designer_meta.get("page_backgrounds")
    if isinstance(_pbg_meta, dict):
        for _k, _v in _pbg_meta.items():
            try:
                _pn = int(_k)
            except (TypeError, ValueError):
                continue
            _fn = str(_v or "").strip()
            if not _fn:
                continue
            # Reuse the hardened resolver by faking a template-like shim so the
            # same path-traversal guards apply.
            _shim = type("_BgShim", (), {"background_image": _fn})()
            _page_bg_paths[_pn] = _resolve_background_path(_shim)

    # Group fields by page
    fields = sorted(template.fields, key=lambda f: (f.page or 1, -(f.y or 0)))
    pages_map: Dict[int, List[DocumentField]] = {}
    for f in fields:
        pages_map.setdefault(int(f.page or 1), []).append(f)
    if not pages_map:
        pages_map[1] = []

    for page_no in sorted(pages_map.keys()):
        _bg_for_page = _page_bg_paths.get(page_no, bg_path)
        drew_bg = _draw_background(c, _bg_for_page, page_width, page_height, fit=_bg_fit)
        page_fields = pages_map[page_no]

        if not drew_bg and not page_fields:
            _draw_record_header(c, template, record, page_width, page_height)
            _draw_auto_grid(c, template, record, page_width, page_height)
        elif not page_fields:
            _draw_record_header(c, template, record, page_width, page_height)
        else:
            for field in page_fields:
                key = field.field_key or ""
                if key in resolved_data:
                    val = resolved_data[key]
                else:
                    val = _resolve_record_value(record, key)
                _render_field(c, field, val, record, db,
                              page_width, page_height,
                              demand_id=demand_id,
                              resolved_data=resolved_data)

        # Notes (Content-tab) printed on every page, above the footer.
        if _content_notes:
            _draw_content_notes(c, _content_notes, page_width, page_height)
        _draw_footer(c, page_width, page_height)
        c.showPage()

    c.save()
    return output_path


# ---------------------------------------------------------------------------
# Available field schema (used by the Template Designer field picker)
# ---------------------------------------------------------------------------
def get_available_fields(data_source: str) -> list:
    """Return a list of field tokens grouped for the Template Designer sidebar.

    Each item: { key, label, group, arabic? }
    """
    schemas = {
        "candidate": [
            # ── Customer (the agency / OEP office) ───────────────────────────
            ("customer_name", "Customer Name", "Customer", False),
            ("customer_name_arabic", "Customer Name (Arabic)", "Customer", True),
            ("customer_owner_name", "Customer Owner Name", "Customer", False),
            ("customer_oep_license", "OEP License Number", "Customer", False),
            ("customer_emigrant_office", "Customer Emigrant Office", "Customer", False),
            ("customer_office_name", "Customer Emigrant Office Name", "Customer", False),
            ("customer_office_city", "Customer Emigrant Office City", "Customer", False),
            ("customer_address", "Customer Address", "Customer", False),
            ("customer_address_arabic", "Customer Address (Arabic)", "Customer", True),
            ("customer_phone", "Customer Phone", "Customer", False),
            ("customer_mobile", "Customer Mobile", "Customer", False),
            ("customer_fax", "Customer Fax", "Customer", False),
            ("customer_email", "Customer Email", "Customer", False),
            ("customer_subdomain", "Customer Subdomain", "Customer", False),
            ("customer_slug", "Customer Slug", "Customer", False),
            ("customer_file_prefix", "Customer File Prefix", "Customer", False),
            ("customer_starting_point", "Customer Starting Point", "Customer", False),
            ("customer_status", "Customer Status", "Customer", False),
            ("customer_plan", "Customer Plan Name", "Customer", False),
            ("customer_expiry_date", "Customer Expiry Date", "Customer", False),
            # ── Candidate Identity ────────────────────────────────────────────
            ("full_name", "Candidate Name", "Candidate", False),
            ("name_arabic", "Candidate Name (Arabic)", "Candidate", True),
            ("father_name", "Father Name", "Candidate", False),
            ("father_name_arabic", "Father Name (Arabic)", "Candidate", True),
            ("mother_name", "Mother Name", "Candidate", False),
            ("cnic", "CNIC Number", "Candidate", False),
            ("nadra_token_no", "NADRA Token No", "Candidate", False),
            ("date_of_birth", "Date of Birth", "Candidate", False),
            ("nationality", "Nationality", "Candidate", False),
            ("address", "Address", "Candidate", False),
            ("phone", "Phone Number", "Candidate", False),
            ("email", "Email", "Candidate", False),
            ("gender", "Gender", "Candidate", False),
            ("marital_status", "Marital Status", "Candidate", False),
            ("religion", "Religion", "Candidate", False),
            ("place_of_birth", "Place of Birth", "Candidate", False),
            ("place_of_birth_arabic", "Place of Birth (Arabic)", "Candidate", True),
            ("tehsil", "Tehsil", "Candidate", False),
            ("district", "District", "Candidate", False),
            ("province", "Province", "Candidate", False),
            ("qualification", "Qualification", "Candidate", False),
            ("profession", "Profession / Trade", "Candidate", False),
            # ── Passport ─────────────────────────────────────────────────────
            ("passport_no", "Passport Number", "Passport", False),
            ("passport_issue_date", "Passport Issue Date", "Passport", False),
            ("passport_expiry_date", "Passport Expiry Date", "Passport", False),
            ("issuing_authority", "Passport Issue Authority", "Passport", False),
            ("issuing_authority_arabic", "Passport Issue Authority (Arabic)", "Passport", True),
            ("passport_issue_place", "Passport Issue Place", "Passport", False),
            ("passport_receive_date", "Passport Receive Date", "Passport", False),
            ("passport_send_date", "Passport Send Date", "Passport", False),
            ("passport_consignment_no", "Passport Consignment No", "Passport", False),
            ("passport_courier_name", "Passport Courier Name", "Passport", False),
            ("passport_receive_date_2", "Passport Receive Date 2", "Passport", False),
            # ── Selected Trade (visa-category line item) ──────────────────────
            ("selected_trade_sr", "Selected Trade Sr#", "Selected Trade", False),
            ("selected_trade_visa_category", "Selected Trade Visa Category", "Selected Trade", False),
            ("selected_trade_qty", "Selected Trade Qty", "Selected Trade", False),
            ("selected_trade_assigned", "Selected Trade Assigned", "Selected Trade", False),
            ("selected_trade_available", "Selected Trade Available", "Selected Trade", False),
            ("salary", "Selected Trade Salary", "Selected Trade", False),
            ("contract_years", "Contract Years", "Selected Trade", False),
            ("contract_period", "Contract Period", "Selected Trade", False),
            # ── Visa File (demand / sponsor) ──────────────────────────────────
            ("file_number", "File Number", "Visa File", False),
            ("company_name", "Client Name", "Visa File", False),
            ("sponsor_name", "Sponsor Name", "Visa File", False),
            ("sponsor_name_arabic", "Sponsor Name (Arabic)", "Visa File", True),
            ("sponsor_address", "Sponsor Address", "Visa File", False),
            ("sponsor_address_arabic", "Sponsor Address (Arabic)", "Visa File", True),
            ("sponsor_phone", "Sponsor Phone", "Visa File", False),
            ("visa_number", "Visa Number", "Visa File", False),
            ("country", "Country", "Visa File", False),
            ("embassy", "Embassy", "Visa File", False),
            ("permission_no", "Permission No", "Visa File", False),
            ("permission_date", "Permission Date", "Visa File", False),
            # ── Next of Kin ───────────────────────────────────────────────────
            ("next_of_kin_name", "Next of Kin Name", "Next of Kin", False),
            ("next_of_kin_nic", "Next of Kin NIC", "Next of Kin", False),
            ("next_of_kin_relation", "Next of Kin Relation", "Next of Kin", False),
            # ── Workflow / Tracking ───────────────────────────────────────────
            ("protector_no", "Protector No", "Workflow", False),
            ("protector_date", "Protector Date", "Workflow", False),
            ("gamca_number", "GAMCA Number", "Workflow", False),
            ("medical_date", "Medical Date", "Workflow", False),
            ("e_number", "E-Number", "Workflow", False),
            ("flight_no", "Flight Number", "Workflow", False),
            ("destination", "Destination", "Workflow", False),
            ("ticket_no", "Ticket Number", "Workflow", False),
            ("status", "Status", "Workflow", False),
            # ── Media ─────────────────────────────────────────────────────────
            ("photo", "Candidate Photo", "Media", False),
        ],
        "demand": [
            # Demand file basics
            ("file_number", "File Number", "Demand File", False),
            ("receiving_date", "Receiving Date", "Demand File", False),
            ("permission_no", "Permission No", "Demand File", False),
            ("permission_date", "Permission Date", "Demand File", False),
            ("reference", "Reference", "Demand File", False),
            ("notes", "Notes", "Demand File", False),
            # Sponsor
            ("sponsor_name", "Sponsor Name", "Sponsor", False),
            ("sponsor_name_arabic", "Sponsor Name (Arabic)", "Sponsor", True),
            ("sponsor_address", "Sponsor Address", "Sponsor", False),
            ("sponsor_address_arabic", "Sponsor Address (Arabic)", "Sponsor", True),
            ("sponsor_phone", "Sponsor Phone", "Sponsor", False),
            ("sponsor_alt_phone", "Sponsor Alt Phone", "Sponsor", False),
            # Visa
            ("visa_number", "Visa Number", "Visa", False),
            ("bataka_number", "Bataka Number", "Visa", False),
            ("visa_issue_date", "Visa Issue Date", "Visa", False),
            ("visa_issue_date_hijri", "Visa Issue Date (Hijri)", "Visa", False),
            ("country", "Country", "Visa", False),
            ("embassy", "Embassy", "Visa", False),
            ("embassy_city", "Embassy City", "Visa", False),
            ("visa_quota", "Visa Quota", "Visa", False),
            ("benefits", "Benefits", "Visa", False),
            ("status", "Status", "Visa", False),
        ],
        "client": [
            ("company_name", "Company Name", "Client", False),
            ("company_name_arabic", "Company Name (Arabic)", "Client", True),
            ("owner_name", "Owner Name", "Client", False),
            ("oep_license_number", "OEP License No", "Client", False),
            ("client_type", "Client Type", "Client", False),
            ("country", "Country", "Client", False),
            ("city", "City", "Client", False),
            ("address", "Address", "Client", False),
            ("phone", "Phone", "Client", False),
            ("mobile", "Mobile", "Client", False),
            ("email", "Email", "Client", False),
            ("website", "Website", "Client", False),
            ("subdomain", "Subdomain", "Client", False),
            ("slug", "Slug", "Client", False),
            ("file_prefix", "File Prefix", "Client", False),
            ("starting_point", "Starting Point", "Client", False),
            ("status", "Status", "Client", False),
        ],
        # "agent" schema removed 2026-07-30 with the Sub-Agents feature.
    }
    base = schemas.get(data_source, [])
    base = base + [
        ("__today__", "[System] Today's Date", "System", False),
        ("__now__", "[System] Date + Time", "System", False),
    ]
    return [
        {"key": k, "label": l, "group": g, "arabic": ar}
        for k, l, g, ar in base
    ]
