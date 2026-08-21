import datetime
import json
import logging
import re
import threading
import time
from types import SimpleNamespace
from typing import Dict, Optional

# AUDIT FIX (2026-07-07): module-level structured logger (matches the
# "dtc.<module>" convention used across the app). Replaces the bare debug
# print() call in the exception handler below — exception-handling logic is
# unchanged, only the reporting call now goes through the logging framework.
log = logging.getLogger("dtc.overlay_engine")


# ---------------------------------------------------------------------------
# Process-level TTL cache for CompanySettings
# ---------------------------------------------------------------------------
# CompanySettings is a single-row "agency profile" table that almost never
# changes at runtime.  resolve_all_related_data_for_record() is called on
# EVERY PDF/print request and previously issued a full DB round-trip for this
# row on each call.  A 60-second TTL process cache eliminates the hot path
# for the overwhelming majority of requests while still picking up admin
# changes within one minute.
#
# The cache is keyed by the SQLAlchemy Session's bind URL (= the DB file path
# in SQLite) so multi-tenant setups with different bind paths don't cross-
# contaminate.  The lock makes it safe under uvicorn's multi-worker threading
# even though each worker process has its own cache.
# ---------------------------------------------------------------------------
_cs_cache_lock = threading.Lock()
_cs_cache: dict = {}          # key → (detached settings snapshot | None, expiry_epoch)
_CS_TTL = 60.0                # seconds before a cached row is considered stale


# ---------------------------------------------------------------------------
# Short-alias rules (single source of truth)
# ---------------------------------------------------------------------------
# AUDIT 2026-07-30. resolve_overlay_fields() dumps every column of every
# related row under a prefix and then exposes a SHORT alias for it, because the
# Template Designer palette advertises short names. These two constants define
# that mapping and are deliberately module-level and public: the resolver
# applies them (section 5 of resolve_overlay_fields) and the designer palette
# enumerates them (documents.designer_palette), so the set of DRAGGABLE fields
# and the set of RESOLVABLE fields are computed from the same rules and cannot
# drift apart again. Before this, both sides were hand-written lists and they
# had drifted badly: 145 resolvable columns were not draggable at all (the
# operator's missing "sponsor id / bataqa no."), and 60 palette entries
# resolved to nothing and printed permanently blank.
#
# PRECEDENCE, most specific first. A short name owned by several tables
# resolves to the one nearest the document's subject: `status` on a candidate
# document means the candidate's status, not the client's.
ALIAS_PRECEDENCE = ("candidate_", "job_", "demand_", "client_")

# Housekeeping columns are deliberately NOT aliased. `id`, `created_at` and the
# *_by_id audit columns are not document data, and aliasing them would put
# meaningless entries in the palette and let `id` mean four different things.
NO_ALIAS_COLUMNS = frozenset({
    "id", "created_at", "updated_at", "created_by_id", "updated_by_id",
    "client_id", "demand_id", "latest_demand_id", "custom_fields",
})

# Prefix → (model import path, human group label) for the palette sweep. Order
# matches ALIAS_PRECEDENCE so the group a short name lands in is the same table
# the resolver will actually read it from.
ALIAS_SOURCE_MODELS = (
    ("candidate_", "app.models.candidate", "Candidate", "Candidate"),
    ("job_",       "app.models.demand",    "JobCategory", "Selected Trade"),
    ("demand_",    "app.models.demand",    "Demand",    "Visa File"),
    ("client_",    "app.models.client",    "Client",    "Client"),
)


def alias_short_name(column: str) -> Optional[str]:
    """Return the short palette name for a prefixed column, or None if the
    column is deliberately not aliased (housekeeping / ambiguous ids)."""
    if not column or column in NO_ALIAS_COLUMNS:
        return None
    return column


def resolvable_short_keys() -> Dict[str, str]:
    """Enumerate every SHORT field key the resolver can produce from the ORM
    models, mapped to the palette group it belongs to.

    Derived from the model metadata rather than a hand-written list, so adding a
    column to Candidate/Demand/JobCategory/Client makes it draggable in the
    Template Designer automatically. This is what keeps the palette complete;
    see the ALIAS_PRECEDENCE comment above for why that matters.

    Precedence is honoured: the first prefix to claim a short name owns it.
    """
    import importlib

    out: Dict[str, str] = {}
    for _prefix, module_path, model_name, group in ALIAS_SOURCE_MODELS:
        try:
            model = getattr(importlib.import_module(module_path), model_name)
        except Exception:  # pragma: no cover - a missing model must not 500 the palette
            log.warning("palette sweep: cannot import %s.%s", module_path, model_name)
            continue
        for col in model.__table__.columns:
            short = alias_short_name(col.name)
            if short and short not in out:
                out[short] = group
    return out


def _get_cached_company_settings(db):
    """Return CompanySettings (or None) using a 60-second process-level TTL cache.

    Falls back to a live DB query on first call, after TTL expiry, or on any
    exception — the cache is purely additive and never masks real errors to the
    caller.
    """
    # Derive a stable cache key from the underlying connection URL so different
    # DB binds (multi-tenant) never share a cached row.
    try:
        bind_key = str(db.bind.url) if db.bind is not None else "default"
    except Exception:
        bind_key = "default"

    now = time.monotonic()
    with _cs_cache_lock:
        entry = _cs_cache.get(bind_key)
        if entry is not None:
            cached_obj, expiry = entry
            if now < expiry:
                return cached_obj          # ← fast path: cached hit

    # Slow path: query DB (outside the lock to avoid blocking other threads).
    try:
        from app.models import CompanySettings
        row = db.query(CompanySettings).order_by(CompanySettings.id.asc()).first()
        # Never cache a live ORM instance across requests. The creating Session
        # is closed at the end of its request and SQLAlchemy may expire/detach
        # the object, causing later cache hits to raise DetachedInstanceError.
        # A plain immutable-by-convention namespace keeps only scalar columns.
        company = (
            SimpleNamespace(
                **{
                    column.name: getattr(row, column.name)
                    for column in CompanySettings.__table__.columns
                }
            )
            if row is not None
            else None
        )
    except Exception:
        company = None

    with _cs_cache_lock:
        _cs_cache[bind_key] = (company, now + _CS_TTL)

    return company


def invalidate_company_settings_cache() -> None:
    """Explicitly flush the CompanySettings TTL cache.

    Call this from the admin endpoint whenever CompanySettings are saved so
    the next request sees the updated row immediately rather than waiting for
    TTL expiry.
    """
    with _cs_cache_lock:
        _cs_cache.clear()
    log.debug("CompanySettings process cache invalidated")


# ---------------------------------------------------------------------------
# Designer meta-fence parser
# ---------------------------------------------------------------------------
# The designer persists a few non-coordinate-mapped settings (letterhead toggle,
# depositor toggle, free-form CONTENT-tab notes) inside an HTML-comment "fence"
# appended to the template's description column, so we don't need extra DB
# columns. The fence looks like:
#
#     "Standard OEP form\n<!--designer:{\"letterhead\":true,\"notes\":\"...\"}-->"
#
# Both the print HTML endpoint and the PDF generator call
# `parse_designer_meta(description)` to retrieve `{letterhead, depositor, notes}`
# so the saved Content-tab notes actually appear on the printed page (audit
# Fix 3) and the letterhead/depositor flags drive their respective renderers.
_DESIGNER_FENCE_RE = re.compile(r"<!--designer:(.+?)-->", re.DOTALL)


def parse_designer_meta(description: Optional[str]) -> Dict:
    """Return the parsed designer meta dict from a template description.

    Always returns a dict -- never None -- so callers can do
    `meta.get('notes', '')` without a None-guard. Malformed fences degrade
    silently to an empty dict.
    """
    if not description:
        return {}
    m = _DESIGNER_FENCE_RE.search(description)
    if not m:
        return {}
    try:
        data = json.loads(m.group(1))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {}


def strip_designer_meta(description: Optional[str]) -> str:
    """Return the description with the designer meta-fence stripped -- useful
    when you want to show the visible portion to the user (e.g. on the
    documents list page)."""
    if not description:
        return ""
    return _DESIGNER_FENCE_RE.sub("", description).strip()


def resolve_overlay_fields(
    candidate=None,
    demand=None,
    job_category=None,
    client=None,
    embassy_choice: Optional[str] = None,
    company=None,
    db=None,
) -> Dict[str, str]:
    """
    Data mapping and overlay engine resolving fields with null-safe fallbacks.
    Provides stable overlay field names matching UI template expectations.
    """
    fields = {}

    # ------------------------------------------------------------------
    # 0. Customer / OEP (the agency itself) -- comes from company_settings.
    # The official OEP form prints the agency / recruiting-office name at the
    # top-left of every document via {{customer_name}}. These MUST be
    # resolved so the Agency Name (and OEP licence, owner, address, etc.)
    # populate on the printed document. (Problem 2)
    # ------------------------------------------------------------------
    # AUDIT 2026-07-30: this block used to be skipped entirely when there was no
    # company_settings row, which silently removed all 21 `customer_*` keys. The
    # designer palette still offered them, so on a deployment that had not filled
    # in the agency profile yet -- i.e. every fresh install -- those fields could
    # be dragged onto a form and then printed nothing, with no error anywhere.
    # The keys are now always defined (empty when unknown), so a blank value
    # means "not configured" rather than "field does not exist".
    _co = company if company is not None else SimpleNamespace()
    cs_map = {
        "customer_name":           getattr(_co, "company_name", "") or "",
        "customer_name_arabic":    getattr(_co, "company_name_arabic", "") or "",
        "customer_owner_name":     getattr(_co, "owner_name", "") or "",
        "customer_oep_license":    getattr(_co, "oep_license_number", "") or "",
        "customer_address":        getattr(_co, "address", "") or "",
        "customer_address_arabic": getattr(_co, "address_arabic", "") or "",
        "customer_phone":          getattr(_co, "phone", "") or "",
        "customer_mobile":         getattr(_co, "mobile", "") or "",
        "customer_fax":            getattr(_co, "fax", "") or "",
        "customer_email":          getattr(_co, "email", "") or "",
        "customer_website":        getattr(_co, "website", "") or "",
        "customer_subdomain":      getattr(_co, "subdomain", "") or "",
        "customer_slug":           getattr(_co, "slug", "") or "",
        "customer_file_prefix":    getattr(_co, "file_prefix", "") or "",
        "customer_starting_point": str(getattr(_co, "starting_point", "") or ""),
        "customer_status":         getattr(_co, "status", "") or "",
        "customer_plan":           getattr(_co, "plan", "") or "",
        # Office name/city default to the company name + address city when
        # the dedicated columns don't exist on company_settings.
        "customer_office_name":    getattr(_co, "company_name", "") or "",
        "customer_emigrant_office": getattr(_co, "company_name", "") or "",
    }
    # Always define the key, even when the column is NULL. Leaving it out
    # entirely made the palette's "Customer Expiry Date" entry unresolvable
    # on any deployment that had not set an expiry, so a field dropped from
    # it rendered blank AND was indistinguishable from a typo.
    ed = getattr(_co, "expiry_date", None)
    if isinstance(ed, (datetime.date, datetime.datetime)):
        cs_map["customer_expiry_date"] = ed.strftime("%d-%b-%Y")
    else:
        cs_map["customer_expiry_date"] = str(ed) if ed else ""
    # derive office city from the tail of the address if present
    addr = (getattr(_co, "address", "") or "")
    cs_map["customer_office_city"] = ""
    for token in ("Sialkot", "Lahore", "Karachi", "Islamabad", "Daska", "Gujranwala"):
        if token.lower() in addr.lower():
            cs_map["customer_office_city"] = token
            break
    fields.update(cs_map)

    today = datetime.date.today()
    fields["__today__"] = today.strftime("%d-%b-%Y")
    fields["__now__"] = datetime.datetime.now().strftime("%d-%b-%Y %H:%M:%S")

    def extract(obj, prefix=""):
        if not obj:
            return
        for col in obj.__table__.columns:
            val = getattr(obj, col.name)
            if val is None:
                val = ""
            elif isinstance(val, (datetime.date, datetime.datetime)):
                val = val.strftime("%d-%b-%Y")
            fields[f"{prefix}{col.name}"] = str(val)

    # 1. Client fields
    extract(client, "client_")
    if client:
        fields["company_name"] = client.company_name or ""
        fields["client_type"] = client.client_type or ""
        fields["sponsor_name"] = client.sponsor_name or fields.get("company_name", "")

    # 2. Demand fields
    extract(demand, "demand_")
    if demand:
        fields["file_number"] = demand.file_number or ""
        fields["visa_number"] = demand.visa_number or ""
        fields["visa_issue_date"] = fields.get("demand_visa_issue_date", "")
        fields["permission_no"] = demand.permission_no or ""
        fields["permission_date"] = fields.get("demand_permission_date", "")
        fields["sponsor_name"] = demand.sponsor_name or fields.get("sponsor_name", "")
        fields["sponsor_name_arabic"] = demand.sponsor_name_arabic or ""
        fields["sponsor_address"] = demand.sponsor_address or ""
        fields["sponsor_address_arabic"] = demand.sponsor_address_arabic or ""
        fields["sponsor_phone"] = demand.sponsor_phone or ""
        fields["country"] = demand.country or "Saudi Arabia"
        
        # Override with specifically selected embassy for this assignment if provided
        fields["embassy"] = embassy_choice or demand.embassy or ""
        fields["visa_quota"] = str(demand.visa_quota) if demand.visa_quota else ""

    # 3. Job Category (Trade) fields
    extract(job_category, "job_")
    if job_category:
        fields["profession"] = job_category.trade or ""
        # The official OEP visa form binds the Occupation cell to the SELECTED
        # trade/visa-category line item via {{selected_trade_visa_category}}.
        # Mirror it so dropped fields resolve correctly. (Problem 3)
        fields["selected_trade_visa_category"] = job_category.trade or ""
        fields["trade_visa_category"] = job_category.trade or ""
        fields["salary"] = str(job_category.salary) if job_category.salary else ""
        # contract_period -- alias for contract_years (formatted as "N years")
        cy = getattr(job_category, "contract_years", None)
        fields["contract_years"] = str(cy) if cy else ""
        fields["contract_period"] = f"{cy} years" if cy else ""
        if getattr(job_category, 'custom_fields', None) and isinstance(job_category.custom_fields, dict):
            for k, v in job_category.custom_fields.items():
                fields[f"job_custom_{k}"] = str(v) if v is not None else ""

        # Selected-Trade flat fields: resolve qty/assigned/available from DB
        # when a db session is available (best-effort, no crash on failure).
        qty = int(getattr(job_category, "quantity", 0) or 0)
        fields["selected_trade_qty"] = str(qty)
        try:
            if db is not None:
                from app.models import CandidateAssignment
                assigned = db.query(CandidateAssignment).filter(
                    CandidateAssignment.job_category_id == job_category.id
                ).count()
            else:
                assigned = 0
        except Exception:
            assigned = 0
        fields["selected_trade_assigned"]  = str(assigned)
        fields["selected_trade_available"] = str(max(0, qty - assigned))
        # Sr# is position of this job_category among its demand's categories
        try:
            if db is not None:
                from app.models import JobCategory as _JC
                rows = db.query(_JC.id).filter(
                    _JC.demand_id == job_category.demand_id
                ).order_by(_JC.id).all()
                sr = next((i + 1 for i, r in enumerate(rows) if r[0] == job_category.id), 1)
            else:
                sr = 1
        except Exception:
            sr = 1
        fields["selected_trade_sr"] = str(sr)

    # 4. Candidate fields
    extract(candidate, "candidate_")
    if candidate:
        fields["full_name"] = candidate.full_name or ""
        fields["name_arabic"] = candidate.name_arabic or ""
        fields["father_name"] = candidate.father_name or ""
        fields["father_name_arabic"] = candidate.father_name_arabic or ""
        # Occupation in Arabic (المهنة) -- the legacy DB never stored an Arabic
        # occupation, so we translate the assigned trade into Arabic exactly like
        # the reference portal does. Priority:
        #   1. a dedicated candidate.profession_arabic column (if it ever exists)
        #   2. Arabic translation of the candidate's own profession text
        #   3. Arabic translation of the selected trade / visa category
        from app.services.occupation_arabic import occupation_to_arabic
        _occ_src = (
            getattr(candidate, "profession_arabic", "") or
            (occupation_to_arabic(candidate.profession) if candidate.profession else "") or
            occupation_to_arabic(fields.get("selected_trade_visa_category", "")) or
            ""
        )
        fields["profession_arabic"] = _occ_src
        # Also expose an Arabic alias for the selected-trade token so visa forms
        # that bind Occupation to {{selected_trade_visa_category_arabic}} resolve.
        fields["selected_trade_visa_category_arabic"] = occupation_to_arabic(
            fields.get("selected_trade_visa_category", "")
        )
        fields["passport_no"] = candidate.passport_no or ""
        fields["cnic"] = candidate.cnic or ""
        fields["date_of_birth"] = fields.get("candidate_date_of_birth", "")
        fields["place_of_birth"] = candidate.place_of_birth or ""
        fields["place_of_birth_arabic"] = getattr(candidate, "place_of_birth_arabic", "") or ""
        fields["nationality"] = candidate.nationality or "PAKISTANI"
        fields["address"] = candidate.address or ""
        fields["phone"] = candidate.phone or ""
        fields["email"] = candidate.email or ""
        if candidate.profession:
            fields["profession"] = candidate.profession

        fields["passport_issue_date"] = fields.get("candidate_passport_issue_date", "")
        fields["passport_expiry_date"] = fields.get("candidate_passport_expiry_date", "")
        fields["passport_issue_place"] = candidate.passport_issue_place or ""
        fields["religion"] = candidate.religion or "Islam"

        # Passport tracking fields (populated when available on the model)
        fields["passport_receive_date"]   = fields.get("candidate_passport_receive_date", "")
        fields["passport_send_date"]      = fields.get("candidate_passport_send_date", "")
        fields["passport_consignment_no"] = getattr(candidate, "passport_consignment_no", "") or ""
        fields["passport_courier_name"]   = getattr(candidate, "passport_courier_name", "") or ""
        fields["passport_receive_date_2"] = fields.get("candidate_passport_receive_date_2", "")
        
        fields["next_of_kin_name"] = candidate.next_of_kin_name or ""
        fields["next_of_kin_relation"] = candidate.next_of_kin_relation or ""

        fields["protector_no"] = candidate.protector_no or ""
        fields["protector_date"] = fields.get("candidate_protector_date", "")
        fields["destination"] = candidate.destination or fields.get("country", "")
        fields["e_number"] = candidate.e_number or ""

        # Candidate may carry its own permission_no/permission_date. Prefer
        # candidate's value over demand's (the candidate is the document subject).
        cand_perm = candidate.permission_no or ""
        if cand_perm:
            fields["permission_no"] = cand_perm
        cand_perm_date = fields.get("candidate_permission_date", "")
        if cand_perm_date:
            fields["permission_date"] = cand_perm_date

        # --- DERIVED CHECKBOX FIELDS ----------------------------------------
        # gender_male / gender_female -- for OEP/visa forms that have separate
        # tick boxes per gender. Engine treats checkbox fields as truthy when
        # value is non-empty/non-zero, so we provide either "1" or "".
        gender = (candidate.gender or "").strip().lower()
        fields["gender_male"]    = "1" if gender.startswith("m") else ""
        fields["gender_female"]  = "1" if gender.startswith("f") else ""

        # marital_single / married -- derived from marital_status field if it
        # exists on the candidate model.
        ms = (getattr(candidate, "marital_status", "") or "").strip().lower()
        fields["marital_single"]   = "1" if ms.startswith("s") else ""
        fields["marital_married"]  = "1" if ms.startswith("m") else ""

        # religion_islam / religion_christian -- common visa-form checkboxes
        rel = (candidate.religion or "").strip().lower()
        fields["religion_islam"]      = "1" if "islam" in rel or "muslim" in rel else ""
        fields["religion_christian"]  = "1" if "christ" in rel else ""

        # Convenience: city/district/province exposed flat
        fields["district"] = getattr(candidate, "district", "") or getattr(candidate, "city", "") or ""
        fields["province"] = getattr(candidate, "province", "") or ""
        fields["qualification"] = getattr(candidate, "qualification", "") or getattr(candidate, "education", "") or ""

        # Next-of-kin CNIC (may or may not exist on the candidate model)
        fields["next_of_kin_nic"] = getattr(candidate, "next_of_kin_cnic", "") or \
                                    getattr(candidate, "next_of_kin_nic", "") or ""

    # ------------------------------------------------------------------
    # 4b. LOOKUP-BACKED FIELDS
    # ------------------------------------------------------------------
    # These three groups are offered by the designer palette but had no resolver
    # at all, so every one of them printed blank forever (found by diffing the
    # palette against the resolver's real output -- see
    # tests/test_designer_palette.py).

    # embassy_city -- the palette offers it next to `embassy`. The city lives on
    # the Embassy lookup row, matched by the embassy name already resolved above.
    fields.setdefault("embassy_city", "")
    _embassy_name = (fields.get("embassy") or "").strip()
    if db is not None and _embassy_name:
        try:
            from app.models.lookups import Embassy as _Embassy
            _row = (
                db.query(_Embassy)
                .filter(_Embassy.name.ilike(f"%{_embassy_name}%"))
                .first()
            )
            if _row is not None:
                fields["embassy_city"] = _row.city or ""
        except Exception:
            # A lookup miss must never break a document render.
            pass
    if not fields["embassy_city"] and _embassy_name:
        # The embassy is habitually recorded AS a city ("Islamabad", "Karachi"),
        # so fall back to the name itself rather than printing nothing.
        fields["embassy_city"] = _embassy_name

    # depositor_* -- the bank deposit slips print who paid the fee. There is no
    # per-candidate depositor column; the office keeps a Depositors lookup and
    # the slip uses the configured one. Resolve the first (default) row so the
    # slip prints a usable value instead of nothing.
    for _dk in ("depositor_first_name", "depositor_last_name",
                "depositor_full_name", "depositor_mobile", "depositor_cnic"):
        fields.setdefault(_dk, "")
    if db is not None:
        try:
            from app.models.lookups import Depositor as _Depositor
            _dep = db.query(_Depositor).order_by(_Depositor.id).first()
            if _dep is not None:
                _first = (_dep.first_name or "").strip()
                _last = (_dep.last_name or "").strip()
                fields["depositor_first_name"] = _first
                fields["depositor_last_name"] = _last
                fields["depositor_full_name"] = (
                    (_dep.full_name or "").strip() or f"{_first} {_last}".strip()
                )
                fields["depositor_mobile"] = _dep.mobile or ""
                fields["depositor_cnic"] = _dep.cnic or ""
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 5. SHORT-ALIAS BACKFILL
    # ------------------------------------------------------------------
    # AUDIT 2026-07-30 (user report: "sponsor id / bataqa no. data field
    # missing in documents coordinate engine").
    #
    # extract() above dumps every column of every related row under a prefix
    # ("candidate_", "demand_", "job_", "client_"). Everything after it then
    # hand-wrote a SHORT alias for *some* of those columns -- full_name,
    # cnic, sponsor_phone and so on. The designer palette advertises the SHORT
    # names, so any column that nobody remembered to alias could not be placed
    # on a form at all, and any palette entry whose short name was never
    # assigned rendered permanently blank. Two real examples the operator hit:
    # `bataka_number` (on Demand) and `sponsor_id` (on Client) were both live
    # columns with data, invisible to the designer.
    #
    # Maintaining that list by hand cannot work -- it drifts every time a column
    # is added. So every prefixed key now gets its short alias automatically,
    # and the explicit assignments above still win because we only fill a key
    # that is not already present.
    #
    # The precedence order and the not-aliased set live at module level
    # (ALIAS_PRECEDENCE / NO_ALIAS_COLUMNS) because the designer palette
    # enumerates the very same rules to decide what is draggable. Keeping one
    # copy is what stops the two sides drifting apart again.
    for _prefix in ALIAS_PRECEDENCE:
        for _key in [k for k in fields if k.startswith(_prefix)]:
            _short = alias_short_name(_key[len(_prefix):])
            if not _short:
                continue
            # setdefault, not assignment: an explicit mapping above (e.g.
            # `nationality` defaulting to "PAKISTANI", or `salary` coming from
            # the job category rather than the candidate) must not be clobbered.
            if _short not in fields:
                fields[_short] = fields[_key]

    # ------------------------------------------------------------------
    # 6. COMPATIBILITY ALIASES
    # ------------------------------------------------------------------
    # The designer palette historically advertised a handful of keys under names
    # that the resolver never produced, so a field dropped from those palette
    # entries rendered blank with no error. The palette now emits the canonical
    # key, but templates saved earlier still reference the old spelling, so both
    # spellings resolve. Left-hand side = the name the palette used to offer.
    for _legacy, _canonical in (
        ("client_name",           "company_name"),
        ("trade_sr_no",           "selected_trade_sr"),
        ("trade_qty",             "selected_trade_qty"),
        ("trade_assigned",        "selected_trade_assigned"),
        ("trade_available",       "selected_trade_available"),
        ("trade_salary",          "salary"),
        ("trade_contract_period", "contract_period"),
        ("accommodation",         "accommodation_allowance"),
        ("passport_consignment",  "passport_consignment_no"),
        ("passport_courier",      "passport_courier_name"),
        ("candidate_city",        "district"),
    ):
        if _legacy not in fields and _canonical in fields:
            fields[_legacy] = fields[_canonical]

    return fields

def resolve_trade_table_rows(db, demand_id: Optional[int]) -> list:
    """Return a list of trade rows for the given demand_id, shaped for the
    designer overlay / print HTML. Each row is::

        {sr, trade, quantity, assigned, available,
         salary, contract_period}

    Returns an empty list when there's no demand or on any DB error so the
    caller can safely render a header-only table in preview mode.
    """
    if not demand_id or not db:
        return []
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
        log.warning("resolve_trade_table_rows failed: %s", e)
        return []

    out = []
    for idx, (jc, assigned) in enumerate(rows, start=1):
        qty = int(jc.quantity or 0)
        ass = int(assigned or 0)
        out.append({
            "_sr": str(idx),
            "trade": jc.trade or "",
            "quantity": qty,
            "_assigned": ass,
            "_available": max(0, qty - ass),
            "salary": jc.salary or "",
            "contract_period": getattr(jc, "contract_period", "") or "",
        })
    return out


def validate_overlay_mappings(template_fields: list, resolved_data: dict) -> list:
    """
    Validates that overlay template fields can be satisfied by the resolved user data.
    Returns a list of field_keys that are missing or empty.
    """
    missing_fields = []
    for f in template_fields:
        if getattr(f, "field_type", "text") == "static":
            continue
        key = getattr(f, "field_key", None)
        if not key:
            continue
        # Check against resolved values
        val = resolved_data.get(key)
        if val is None or str(val).strip() == "":
            missing_fields.append(key)
            
    return missing_fields


def resolve_all_related_data_for_record(db, data_source: str, record_id: int) -> dict:
    """
    Retrieve all related database models for a given data source and record ID,
    and resolve them using the core resolve_overlay_fields mapper.
    """
    candidate = None
    demand = None
    job_category = None
    client = None
    embassy_choice = None

    # The agency / OEP info is the same for every document in this single-tenant
    # deployment -- always load it so {{customer_*}} fields populate. (Problem 2)
    # Use the process-level TTL cache to avoid a DB round-trip on every PDF call.
    company = None
    try:
        company = _get_cached_company_settings(db)
    except Exception:
        company = None

    if not record_id:
        return resolve_overlay_fields(company=company)

    from app.models import Candidate, CandidateAssignment, Client, Demand, JobCategory

    if data_source == "candidate":
        candidate = db.query(Candidate).filter(Candidate.id == record_id).first()
        if candidate:
            row = (
                db.query(CandidateAssignment, JobCategory, Demand, Client)
                .join(JobCategory, JobCategory.id == CandidateAssignment.job_category_id)
                .join(Demand, Demand.id == JobCategory.demand_id)
                .outerjoin(Client, Client.id == Demand.client_id)
                .filter(CandidateAssignment.candidate_id == candidate.id)
                .order_by(CandidateAssignment.assigned_at.desc(), CandidateAssignment.id.desc())
                .first()
            )
            if row:
                assignment, job_category, demand, client = row
                embassy_choice = assignment.embassy or demand.embassy or None
    elif data_source == "demand":
        demand = db.query(Demand).filter(Demand.id == record_id).first()
        if demand:
            client = db.query(Client).filter(Client.id == demand.client_id).first()
            embassy_choice = demand.embassy or None
    elif data_source == "client":
        client = db.query(Client).filter(Client.id == record_id).first()
    # REMOVED 2026-07-30 (operator request): the `agent` data_source branch. The
    # Sub-Agents feature and its Agent/AgentCash models have been deleted, so a
    # template can no longer be bound to an agent record. An unrecognised
    # data_source now falls through to the tenant-only resolve below, which is
    # the same behaviour as any other unknown source and renders the agency
    # header without crashing.

    return resolve_overlay_fields(
        candidate=candidate,
        demand=demand,
        job_category=job_category,
        client=client,
        embassy_choice=embassy_choice,
        company=company,
        db=db,
    )
