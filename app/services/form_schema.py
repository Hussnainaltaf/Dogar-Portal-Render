"""Structured field-schema extraction for scanned government form templates.

Task: "Analyze the uploaded form scans and extract a structured field schema
for the document templates."

The uploaded form scans (Pakistani/Saudi overseas-employment forms) live in
``app/static/pdf_backgrounds/`` and are used as PDF overlay backgrounds:
  oep_form_p1.jpg / oep_form_p2.jpg  — Bureau of Emigration OEP registration form
  saudi_visa_application.jpg          — Saudi visa application (Islamabad)
  visa_application_karachi_letterhead — Saudi visa application (Karachi)
  permission_undertaking_*.jpg        — recruitment permission undertakings
  hbl_form_32a.jpg / nbp_deposit_slip / allied_bank_form7 — bank fee deposit slips

Rather than run flaky OCR at request time, we encode the *analysed* result: a
normalized, machine-readable schema per form. Coordinates from the hand-tuned
overlay layout (PDF points, origin bottom-left, A4 595x842) are converted to
**normalized top-left fractions** (0..1) so the schema is resolution- and
page-size-independent and can drive the React designer or a template import.

For each field we emit:
  field_key   — snake_case data key (maps to a DB column via data_source)
  label       — human label as printed on the form
  field_type  — inferred: text | date | char_cells | photo | barcode | signature
  data_source — inferred owner entity: candidate | demand | client | static
  x, y, w, h  — normalized top-left rectangle (fractions of page)
  page        — 1-based page number (multi-page forms)

``extract_schema(form)`` returns one form; ``all_schemas()`` returns every
known form. The admin endpoint exposes these so an operator can review/import
them as DocumentTemplate field sets.
"""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field

# A4 in PDF points — the coordinate space the overlay layout was authored in.
PAGE_W = 595.0
PAGE_H = 842.0

# --- type / source inference -------------------------------------------------
_DATE_KEYS = {
    "date_of_birth", "passport_issue_date", "passport_expiry_date",
    "permission_date", "receiving_date", "visa_issue_date", "protector_date",
    "__today__",
}
_CHAR_CELL_KEYS = {"cnic", "passport_no"}  # forms print these in boxed cells
_PHOTO_KEYS = {"photo", "candidate_photo"}
_BARCODE_KEYS = {"barcode", "visa_barcode"}
_SIGNATURE_KEYS = {"signature", "applicant_signature"}

# field_key -> owning entity (data_source). Everything else defaults to candidate.
_DEMAND_KEYS = {
    "file_number", "permission_no", "permission_date", "receiving_date",
    "sponsor_name", "sponsor_name_arabic", "sponsor_address", "sponsor_phone",
    "country", "visa_number", "visa_issue_date", "visa_quota", "embassy",
}
_STATIC_KEYS = {"__today__"}


def infer_field_type(field_key: str) -> str:
    if field_key in _DATE_KEYS:
        return "date"
    if field_key in _CHAR_CELL_KEYS:
        return "char_cells"
    if field_key in _PHOTO_KEYS:
        return "photo"
    if field_key in _BARCODE_KEYS:
        return "barcode"
    if field_key in _SIGNATURE_KEYS:
        return "signature"
    return "text"


def infer_data_source(field_key: str, form_default: str) -> str:
    if field_key in _STATIC_KEYS:
        return "static"
    if field_key in _DEMAND_KEYS:
        return "demand"
    return form_default


@dataclass
class SchemaField:
    field_key: str
    label: str
    field_type: str
    data_source: str
    x: float
    y: float
    w: float
    h: float
    page: int = 1

    def to_dict(self) -> dict:
        return {
            "field_key": self.field_key,
            "label": self.label,
            "field_type": self.field_type,
            "data_source": self.data_source,
            # normalized top-left rectangle (fractions, 4 dp)
            "x": round(self.x, 4),
            "y": round(self.y, 4),
            "w": round(self.w, 4),
            "h": round(self.h, 4),
            "page": self.page,
        }


@dataclass
class FormSchema:
    key: str
    title: str
    authority: str
    background_image: str
    default_data_source: str
    fields: list[SchemaField] = dc_field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "title": self.title,
            "authority": self.authority,
            "background_image": self.background_image,
            "default_data_source": self.default_data_source,
            "field_count": len(self.fields),
            "pages": sorted({f.page for f in self.fields}),
            "fields": [f.to_dict() for f in self.fields],
        }


# ---------------------------------------------------------------------------
# Raw analysed layout: (label, field_key, x_pt, y_pt_from_bottom, w_pt, page)
# Heights default to a single text line (~18pt). Converted below to normalized
# top-left rectangles.
# ---------------------------------------------------------------------------
_RAW = {
    "oep_form": {
        "title": "Overseas Employment Promoter (OEP) Registration Form",
        "authority": "Bureau of Emigration & Overseas Employment, Pakistan",
        "background_image": "oep_form_p1.jpg",
        "default_data_source": "candidate",
        "layout": [
            # Page 1 — personal + passport
            ("Full Name", "full_name", 150, 735, 380, 1),
            ("Father Name", "father_name", 150, 705, 380, 1),
            ("CNIC", "cnic", 150, 675, 250, 1),
            ("Date of Birth", "date_of_birth", 410, 675, 170, 1),
            ("Passport No", "passport_no", 150, 645, 250, 1),
            ("Passport Expiry", "passport_expiry_date", 410, 645, 170, 1),
            ("Profession", "profession", 150, 615, 250, 1),
            ("Nationality", "nationality", 410, 615, 170, 1),
            ("Address", "address", 150, 585, 430, 1),
            ("Phone", "phone", 150, 555, 250, 1),
            ("Email", "email", 410, 555, 170, 1),
            ("Today's Date", "__today__", 450, 110, 100, 1),
            # Page 2 — next-of-kin, job & employer
            ("Next of Kin", "next_of_kin_name", 150, 735, 300, 2),
            ("Kin Relation", "next_of_kin_relation", 410, 735, 170, 2),
            ("Job Title", "profession", 150, 655, 250, 2),
            ("Salary", "salary", 410, 655, 170, 2),
            ("Country", "country", 150, 625, 250, 2),
            ("Sponsor Name", "sponsor_name", 150, 595, 430, 2),
        ],
    },
    "saudi_visa_islamabad": {
        "title": "Saudi Arabia Visa Application Form (Islamabad)",
        "authority": "Royal Embassy of Saudi Arabia, Islamabad",
        "background_image": "saudi_visa_application.jpg",
        "default_data_source": "candidate",
        "layout": [
            ("Full Name", "full_name", 66, 668, 470, 1),
            ("Mother's Name", "mother_name", 130, 633, 400, 1),
            ("Date of Birth", "date_of_birth", 130, 610, 165, 1),
            ("Place of Birth", "place_of_birth", 400, 605, 140, 1),
            ("Previous Nationality", "previous_nationality", 175, 590, 105, 1),
            ("Present Nationality", "nationality", 410, 590, 128, 1),
            ("Marital Status", "marital_status", 365, 585, 160, 1),
            ("Religion", "religion", 385, 568, 165, 1),
            ("Place of Issue", "passport_issue_place", 135, 535, 120, 1),
            ("Qualification", "qualification", 258, 535, 78, 1),
            ("Profession", "profession", 360, 535, 185, 1),
            ("Home Telephone", "phone", 240, 504, 300, 1),
            ("Passport No", "passport_no", 434, 453, 115, 1),
            ("Passport Issue Date", "passport_issue_date", 180, 438, 180, 1),
            ("Passport Expiry", "passport_expiry_date", 180, 423, 180, 1),
        ],
    },
    "saudi_visa_karachi": {
        "title": "Saudi Arabia Visa Application Form (Karachi)",
        "authority": "Consulate General of Saudi Arabia, Karachi",
        "background_image": "visa_application_karachi_letterhead.jpg",
        "default_data_source": "candidate",
        "layout": [
            ("Candidate Name", "full_name", 155, 698, 250, 1),
            ("Name (Arabic)", "name_arabic", 410, 698, 165, 1),
            ("Father Name", "father_name", 155, 673, 250, 1),
            ("Father (Arabic)", "father_name_arabic", 410, 673, 165, 1),
            ("Passport No", "passport_no", 155, 648, 180, 1),
            ("Issue Date", "passport_issue_date", 340, 648, 110, 1),
            ("Expiry Date", "passport_expiry_date", 460, 648, 110, 1),
            ("Issue Place", "passport_issue_place", 155, 623, 250, 1),
            ("Date of Birth", "date_of_birth", 410, 623, 165, 1),
            ("Place of Birth", "place_of_birth", 155, 598, 250, 1),
            ("Nationality", "nationality", 410, 598, 165, 1),
            ("Profession", "profession", 155, 573, 250, 1),
            ("Religion", "religion", 410, 573, 165, 1),
            ("Address", "address", 155, 548, 420, 1),
            ("CNIC", "cnic", 155, 523, 250, 1),
            ("Phone", "phone", 410, 523, 165, 1),
        ],
    },
    "permission_undertaking": {
        "title": "Permission / Undertaking for Recruitment",
        "authority": "Protector of Emigrants, Pakistan",
        # AUDIT 2026-08-01: this declared "permission_undertaking_1.jpg", which has
        # never existed in app/static/pdf_backgrounds/. An external audit read that
        # as missing artwork and declined to guess a replacement -- correctly, but
        # the truth is simpler: there is no artwork to find. This document is NOT a
        # coordinate/overlay template at all. init_db.py's TEMPLATE_DEFS allow-list
        # deliberately excludes permission/undertaking letters ("NOT coordinate
        # templates -- they are generated by the letterhead renderer"), and
        # app/services/letterhead_renderer.py owns them via
        # render_demand_permission_request() / render_undertaking(), which typeset
        # them on the company letterhead in HTML.
        #
        # So the entry is a leftover from the pre-letterhead era. The dangling
        # filename is removed rather than replaced: _resolve_background_path()
        # returns None for a name it cannot resolve, so the declaration promised a
        # background that could never be drawn. An empty value states the actual
        # behaviour. The layout below is kept because the field list still documents
        # the data this document consumes, and /api/form-schemas exposes it.
        # Guarded by tests/test_form_schema_backgrounds.py.
        "background_image": "",
        "default_data_source": "demand",
        "layout": [
            ("File Number", "file_number", 150, 740, 300, 1),
            ("Permission No", "permission_no", 150, 715, 300, 1),
            ("Permission Date", "permission_date", 150, 690, 200, 1),
            ("Receiving Date", "receiving_date", 400, 690, 160, 1),
            ("Sponsor Name", "sponsor_name", 150, 660, 400, 1),
            ("Sponsor (Arabic)", "sponsor_name_arabic", 150, 635, 400, 1),
            ("Sponsor Address", "sponsor_address", 150, 610, 400, 1),
            ("Sponsor Phone", "sponsor_phone", 150, 585, 250, 1),
            ("Country", "country", 400, 585, 160, 1),
            ("Visa Number", "visa_number", 150, 560, 250, 1),
            ("Visa Issue Date", "visa_issue_date", 400, 560, 160, 1),
            ("Visa Quota", "visa_quota", 150, 535, 100, 1),
            ("Embassy", "embassy", 250, 535, 300, 1),
            ("Today's Date", "__today__", 450, 110, 100, 1),
        ],
    },
    "bank_fee_deposit": {
        "title": "Bank Fee Deposit Slip (NBP / HBL / Allied)",
        "authority": "National Bank of Pakistan / Habib Bank / Allied Bank",
        "background_image": "nbp_deposit_slip.jpg",
        "default_data_source": "candidate",
        "layout": [
            ("Depositor Name", "full_name", 180, 700, 360, 1),
            ("CNIC", "cnic", 180, 670, 260, 1),
            ("Passport No", "passport_no", 430, 670, 130, 1),
            ("Phone", "phone", 180, 640, 260, 1),
            ("Address", "address", 180, 610, 380, 1),
            ("Today's Date", "__today__", 430, 640, 130, 1),
        ],
    },
}

_LINE_H_PT = 18.0  # nominal text-line height in points


def _build(form_key: str) -> FormSchema:
    raw = _RAW[form_key]
    default_src = raw["default_data_source"]
    fields: list[SchemaField] = []
    for row in raw["layout"]:
        label, key, x_pt, y_pt, w_pt, page = row
        h_pt = _LINE_H_PT
        # Convert PDF (bottom-left origin) → normalized top-left fractions.
        norm_x = x_pt / PAGE_W
        norm_w = w_pt / PAGE_W
        norm_h = h_pt / PAGE_H
        # y_pt is the baseline from the bottom; the field's TOP from the top is
        # (PAGE_H - (y_pt + h_pt)).
        norm_y = (PAGE_H - (y_pt + h_pt)) / PAGE_H
        fields.append(SchemaField(
            field_key=key,
            label=label,
            field_type=infer_field_type(key),
            data_source=infer_data_source(key, default_src),
            x=max(0.0, norm_x),
            y=max(0.0, norm_y),
            w=norm_w,
            h=norm_h,
            page=page,
        ))
    return FormSchema(
        key=form_key,
        title=raw["title"],
        authority=raw["authority"],
        background_image=raw["background_image"],
        default_data_source=default_src,
        fields=fields,
    )


def list_forms() -> list[str]:
    return list(_RAW.keys())


def extract_schema(form_key: str) -> dict | None:
    if form_key not in _RAW:
        return None
    return _build(form_key).to_dict()


def all_schemas() -> dict:
    forms = [_build(k).to_dict() for k in _RAW]
    return {
        "forms": forms,
        "form_count": len(forms),
        "total_fields": sum(f["field_count"] for f in forms),
    }
