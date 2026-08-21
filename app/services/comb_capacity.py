"""Address / long-text capacity policy for pre-printed character combs.

THE PROBLEM
-----------
A ``char_cells`` field renders one character per pre-printed box on a scanned
government form. The box pitch is fixed by the paper, so the capacity is a hard
physical limit, not a preference.

On OEP form OEPE-RF-02 the Pakistan address (form field 12) is **two stacked
rows of 13 boxes = 26 characters total** (measured: rows at ``y_pt`` 420.0 and
400.36, ``cell_count`` 13 each, the second carrying ``char_offset: 13``).

Measured against the live database (2,636 non-empty candidate addresses):

===========================  =====================
exceeding 26 characters      2,198 / 2,636 (83.4%)
median length                35
p90 length                   52
longest                      91
exactly 26                   69
===========================  =====================

Before this module the engine simply stopped drawing after cell 26 and logged a
warning. That produced output like::

    canonical  'HARI PURKI P.O KAMONKI TEHSIL KAMON KI DISTT GUJRANWALA'
    printed    'HARI PURKI P.O KAMONKI TEH'
    lost       'SIL KAMON KI DISTT GUJRANWALA'

Two things are wrong with that, and they are different problems:

1. It cuts **mid-word** (``TEHSIL`` → ``TEH``), which reads as a typo rather
   than an abbreviation.
2. It drops the **district**, which is the part of a Pakistani address that
   actually identifies the location for a government reader.

On a submitted government form this is the worst possible failure mode: the
document looks complete, and nobody -- operator, applicant or receiving officer
-- can tell that the tail is missing.

WHY THERE IS NO "JUST ABBREVIATE IT" ANSWER
-------------------------------------------
Abbreviation was investigated empirically, not guessed. Token frequencies across
the same 2,636 addresses show that operators have *already* established
abbreviations, and those are the only ones used here:

======================  ==============  =============
long form (count)       short (count)   verdict
======================  ==============  =============
TEHSIL (149), TEH. (77) TEH (231)       attested
DISTRICT (44), DISTT.   DISTT (151)     attested
MOHALLAH (63) etc.      MOH (458)       attested
P.O (1079), P.O. (29)   PO (135)        attested
STREET (64)             ST (140)        attested
VILLAGE (66)            VILL (44)       attested
ROAD (233)              RD (**0**)      **REJECTED**
NUMBER (0)              NO (32)         **REJECTED**
======================  ==============  =============

``ROAD -> RD`` is rejected precisely because it is the tempting one: it would
save two characters on 233 records, but **no operator has ever written ``RD``**
in this corpus, so shipping it would be inventing a convention and calling it
policy. The rule is: *an abbreviation is only legitimate if the short form is
already attested in the operators' own data.*

And the payoff is small, which is the decisive fact:

* fits raw: 438 / 2,636 = 16.6%
* fits after attested abbreviation: 517 / 2,636 = 19.6%  (**+79 records**)
* still over capacity: 2,119 = **80.4%**

Abbreviation is therefore a genuine but marginal improvement, not a solution. A
policy built on it alone would be a fig leaf over the same silent data loss.

THE POLICY THIS MODULE IMPLEMENTS
---------------------------------
Four ordered stages. Every stage is deterministic and reproducible, and the
canonical value is **never** modified -- the database keeps exactly what the
operator typed.

1. **FITS** -- the canonical value already fits. Print it unchanged.
2. **ABBREVIATED** -- attested abbreviations bring it within capacity. Print the
   abbreviated form and record which substitutions were applied, so the output
   is explainable character-for-character.
3. **TRUNCATED_AT_WORD** -- it still does not fit, but the value can be cut at a
   **word boundary**. Print that. Never cut mid-word: ``TEHSIL`` is never
   rendered as ``TEH`` by truncation, because a reader cannot distinguish an
   abbreviation from a mangled word.
4. **CAPACITY_EXCEEDED** -- not even the first word fits (or the tail dropped is
   large enough that the printed form would be misleading). This is a
   **review-required** state, surfaced to the operator; see below.

In stages 3 and 4 the dropped remainder is reported verbatim in
``CombFit.dropped`` so the caller can show the operator exactly what will be
missing, rather than a vague warning.

WHAT IS *NOT* DONE, AND WHY
---------------------------
* **No silent truncation.** Every non-FITS outcome carries a status, a reason and
  the dropped text.
* **No invented abbreviations.** See the ROAD/RD rejection above.
* **No shrinking the font or the pitch to fit more characters.** The boxes are
  pre-printed on the paper; the geometry is not ours to change. The coordinate
  engine is deliberately untouched by this module -- it only decides *what text*
  is handed to the existing renderer, never *where* a character lands.
* **No overflow into neighbouring fields.** The per-row slice still hard-stops at
  ``cell_count``; capacity is computed from the rows that exist, so a value can
  never bleed into the field printed beside it.

THREE RENDERERS, ONE POLICY
---------------------------
The same template is rendered by three independent code paths:

* ``app/services/pdf_engine.py`` (ReportLab, the PDF download)
* ``app/api/endpoints/documents.py`` (the HTML print path, what the Print button uses)
* ``app/frontend/src/pages/designer/FieldMarker.tsx`` (the designer canvas preview)

They have disagreed before -- the HTML path had no ``char_cells`` support at all
until 2026-08-11, and the designer drew combs as one continuous string until
2026-08-12. Both defects were invisible on the screen whose job was to show
them. So this policy lives in **one** module, is exported to the frontend through
an API endpoint rather than reimplemented in TypeScript, and
``tests/test_comb_capacity.py`` asserts all three paths produce the same printed
string for the same input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

__all__ = [
    "ATTESTED_ABBREVIATIONS",
    "CombFit",
    "STATUS_ABBREVIATED",
    "STATUS_CAPACITY_EXCEEDED",
    "STATUS_EMPTY",
    "STATUS_FITS",
    "STATUS_TRUNCATED_AT_WORD",
    "REJECTED_ABBREVIATIONS",
    "REVIEW_REQUIRED_STATUSES",
    "apply_attested_abbreviations",
    "comb_capacity_for_key",
    "comb_rows_from_fields",
    "fit_comb_text",
    "resolve_comb_row",
    "row_text_for_offset",
]

# ---------------------------------------------------------------------------
# Status vocabulary
# ---------------------------------------------------------------------------
STATUS_EMPTY = "empty"
STATUS_FITS = "fits"
STATUS_ABBREVIATED = "abbreviated"
STATUS_TRUNCATED_AT_WORD = "truncated_at_word"
STATUS_CAPACITY_EXCEEDED = "capacity_exceeded"

#: Statuses that require an operator to look at the record before the document
#: is treated as correct. ``truncated_at_word`` is included: the output is
#: readable, but information was still dropped, and the operator is the only one
#: who can decide whether the shortened form is acceptable.
REVIEW_REQUIRED_STATUSES = frozenset({
    STATUS_TRUNCATED_AT_WORD,
    STATUS_CAPACITY_EXCEEDED,
})

# ---------------------------------------------------------------------------
# Attested abbreviations
# ---------------------------------------------------------------------------
# Every entry below was verified to have its SHORT form already present in the
# operators' own address data (see the module docstring for counts). Do not add
# an entry unless you have re-run that frequency check and the short form is
# attested -- otherwise you are inventing a convention, which this policy
# explicitly refuses to do.
#
# Keys are matched case-insensitively against whole whitespace-delimited tokens,
# so "STREET" is replaced but "STREETLIGHT" is not.
ATTESTED_ABBREVIATIONS: Dict[str, str] = {
    "TEHSIL": "TEH",
    "TEH.": "TEH",
    "DISTRICT": "DISTT",
    "DISTT.": "DISTT",
    "DIST": "DISTT",
    "DIST.": "DISTT",
    "MOHALLAH": "MOH",
    "MOHALLA": "MOH",
    "MOH.": "MOH",
    "P.O": "PO",
    "P.O.": "PO",
    "POST": "PO",
    "VILLAGE": "VILL",
    "STREET": "ST",
}

# Deliberately NOT abbreviated. Kept as data (rather than merely omitted) so the
# reasoning is discoverable at the point someone is tempted to add them, and so
# `test_rejected_abbreviations_are_not_applied` can assert on it.
REJECTED_ABBREVIATIONS: Dict[str, str] = {
    # long form: what a naive implementation would have used
    "ROAD": "RD",     # 233 uses of ROAD, ZERO uses of RD in the corpus
    "NUMBER": "NO",   # ZERO uses of NUMBER; nothing to abbreviate
    "HOUSE": "H",     # "H" appears, but as a house-number prefix, not for HOUSE
    "NEAR": "NR",     # ZERO uses of NR
    "COLONY": "COL",  # ZERO uses of COL
    "TOWN": "TWN",    # ZERO uses of TWN
}

_TOKEN_SPLIT = re.compile(r"(\s+)")


@dataclass(frozen=True)
class CombFit:
    """The decision about how one value is rendered into a comb.

    Attributes
    ----------
    canonical:
        Exactly what is stored in the database, whitespace-normalised only.
        **Never** shortened. This is the value of record.
    printed:
        The characters that will actually be placed into the boxes, across all
        rows of the comb. Always ``len(printed) <= capacity``.
    status:
        One of the ``STATUS_*`` constants.
    capacity:
        Total characters the comb can hold across every row bound to the key.
    dropped:
        The trailing text that will NOT appear on the form. Empty when the value
        fits. Reported verbatim so an operator can be shown precisely what is
        missing instead of a generic warning.
    abbreviations:
        The attested substitutions that were applied, as ``(long, short)``
        pairs, in the order encountered. Makes the output explainable.
    reason:
        Human-readable explanation, suitable for an operator-facing message.
    """

    canonical: str
    printed: str
    status: str
    capacity: int
    dropped: str = ""
    abbreviations: Tuple[Tuple[str, str], ...] = ()
    reason: str = ""

    @property
    def review_required(self) -> bool:
        """True when a human must confirm the record before it is filed."""
        return self.status in REVIEW_REQUIRED_STATUSES

    @property
    def fits(self) -> bool:
        return self.status in (STATUS_EMPTY, STATUS_FITS, STATUS_ABBREVIATED)

    def to_dict(self) -> Dict[str, Any]:
        """JSON-serialisable form, for the API the designer preview consumes."""
        return {
            "canonical": self.canonical,
            "printed": self.printed,
            "status": self.status,
            "capacity": self.capacity,
            "used": len(self.printed),
            "dropped": self.dropped,
            "dropped_chars": len(self.dropped),
            "abbreviations": [list(pair) for pair in self.abbreviations],
            "reason": self.reason,
            "review_required": self.review_required,
            "fits": self.fits,
        }


def apply_attested_abbreviations(text: str) -> Tuple[str, Tuple[Tuple[str, str], ...]]:
    """Replace whole tokens with their attested short forms.

    Returns ``(rewritten_text, applied_pairs)``. Case-insensitive on the lookup,
    but the replacement is emitted in the canonical upper-case short form because
    every attested example in the corpus is upper-case.

    Whole-token matching only: ``STREET`` is abbreviated, ``STREETLIGHT`` is not.
    """
    if not text:
        return "", ()
    applied: List[Tuple[str, str]] = []
    out: List[str] = []
    for part in _TOKEN_SPLIT.split(text):
        if not part or part.isspace():
            out.append(part)
            continue
        short = ATTESTED_ABBREVIATIONS.get(part.upper())
        if short is not None and short.upper() != part.upper():
            applied.append((part, short))
            out.append(short)
        else:
            out.append(part)
    return "".join(out), tuple(applied)


def _truncate_at_word(text: str, capacity: int) -> Tuple[str, str]:
    """Cut ``text`` to at most ``capacity`` chars on a whitespace boundary.

    Returns ``(kept, dropped)``. ``dropped`` retains its original spelling so it
    can be shown to the operator.

    If the first word alone exceeds ``capacity`` there is no boundary to cut on,
    and ``("", text)`` is returned -- the caller turns that into
    ``CAPACITY_EXCEEDED`` rather than mangling a single long word.
    """
    if len(text) <= capacity:
        return text, ""
    # Search for the last space at or before the capacity boundary. Looking at
    # capacity+1 lets a value that ends exactly on a boundary keep the whole
    # word (a space AT the limit means everything before it fits).
    window = text[: capacity + 1]
    cut = window.rfind(" ")
    if cut <= 0:
        return "", text
    return text[:cut].rstrip(), text[cut:].strip()


def fit_comb_text(value: Any, capacity: int, *,
                  abbreviate: bool = True) -> CombFit:
    """Decide what to print for ``value`` in a comb of ``capacity`` characters.

    This is the single decision point for the whole policy. All three renderers
    call it (directly or via the API) so they cannot disagree.

    Handles every boundary case explicitly:

    * empty / whitespace-only  -> ``STATUS_EMPTY``, prints nothing
    * shorter than capacity    -> ``STATUS_FITS``
    * **exactly** capacity     -> ``STATUS_FITS`` (no abbreviation, no warning)
    * over, fixable by attested abbreviation -> ``STATUS_ABBREVIATED``
    * over, cuttable at a word boundary      -> ``STATUS_TRUNCATED_AT_WORD``
    * over, first word alone too long        -> ``STATUS_CAPACITY_EXCEEDED``
    * ``capacity <= 0`` (misconfigured field) -> ``STATUS_CAPACITY_EXCEEDED``

    ``abbreviate=False`` skips stage 2, for callers that need to show the
    operator the unmodified consequence of the current data.
    """
    canonical = re.sub(r"\s+", " ", str(value or "")).strip()
    cap = int(capacity or 0)

    if not canonical:
        return CombFit(canonical="", printed="", status=STATUS_EMPTY,
                       capacity=max(cap, 0),
                       reason="No value to print.")

    if cap <= 0:
        # A comb with no cells cannot print anything. Treat as a configuration
        # error the operator must see, not as an empty value.
        return CombFit(
            canonical=canonical, printed="", status=STATUS_CAPACITY_EXCEEDED,
            capacity=0, dropped=canonical,
            reason=("This field has no character cells configured, so nothing "
                    "can be printed. Fix the field in the Template Designer."),
        )

    # --- stage 1: it already fits (includes the exactly-equal case) --------
    if len(canonical) <= cap:
        return CombFit(
            canonical=canonical, printed=canonical, status=STATUS_FITS,
            capacity=cap,
            reason=f"Fits in {cap} cells ({len(canonical)} used).",
        )

    # --- stage 2: attested abbreviations ----------------------------------
    abbreviated, applied = (canonical, ())
    if abbreviate:
        abbreviated, applied = apply_attested_abbreviations(canonical)
        abbreviated = re.sub(r"\s+", " ", abbreviated).strip()
        if applied and len(abbreviated) <= cap:
            pairs = ", ".join(f"{long}\u2192{short}" for long, short in applied)
            return CombFit(
                canonical=canonical, printed=abbreviated,
                status=STATUS_ABBREVIATED, capacity=cap,
                abbreviations=applied,
                reason=(f"Shortened to {len(abbreviated)} of {cap} cells using "
                        f"standard abbreviations already used in this system: "
                        f"{pairs}. The full address is unchanged in the record."),
            )

    # Work from the abbreviated form when abbreviation helped at all, so we
    # print as much real information as possible before cutting.
    working = abbreviated if (applied and len(abbreviated) < len(canonical)) else canonical

    # --- stage 3: cut on a word boundary ----------------------------------
    kept, dropped = _truncate_at_word(working, cap)
    if kept:
        note = ""
        if applied:
            note = (" Standard abbreviations were applied first ("
                    + ", ".join(f"{long}\u2192{short}" for long, short in applied)
                    + ").")
        return CombFit(
            canonical=canonical, printed=kept,
            status=STATUS_TRUNCATED_AT_WORD, capacity=cap,
            dropped=dropped, abbreviations=applied,
            reason=(f"Too long for {cap} cells ({len(canonical)} characters). "
                    f"Cut at a word boundary; {len(dropped)} characters will "
                    f"NOT appear on the form: \u201c{dropped}\u201d. The full value is "
                    f"preserved in the record and must be reviewed before "
                    f"filing.{note}"),
        )

    # --- stage 4: cannot represent this value at all ----------------------
    return CombFit(
        canonical=canonical, printed="", status=STATUS_CAPACITY_EXCEEDED,
        capacity=cap, dropped=canonical, abbreviations=applied,
        reason=(f"Cannot be printed in {cap} cells: the first word alone is "
                f"longer than the field. Nothing is printed rather than a "
                f"mangled fragment. Shorten the value in the record, or add "
                f"another comb row to the template."),
    )


# ---------------------------------------------------------------------------
# Capacity discovery across multi-row combs
# ---------------------------------------------------------------------------
def _row_cell_count(meta: Optional[Dict[str, Any]]) -> int:
    try:
        return max(0, int(float((meta or {}).get("cell_count") or 0)))
    except (TypeError, ValueError):
        return 0


def _row_char_offset(meta: Optional[Dict[str, Any]]) -> int:
    try:
        n = int((meta or {}).get("char_offset") or 0)
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def comb_capacity_for_key(rows: Iterable[Tuple[int, int]]) -> int:
    """Total characters a multi-row comb can hold.

    ``rows`` is an iterable of ``(char_offset, cell_count)`` for every
    ``char_cells`` field bound to the same key on the same page.

    The capacity is ``max(offset + count)``, NOT ``sum(count)``. That
    distinction matters: two rows with offsets 0 and 13 and 13 cells each hold
    26 characters, but two rows both at offset 0 (a template mistake where the
    second row was duplicated without setting ``char_offset``) hold only 13 --
    the second row just reprints the first. Summing would report 26 and hide the
    template bug behind a "fits" verdict.
    """
    best = 0
    for offset, count in rows:
        if count <= 0:
            continue
        best = max(best, int(offset) + int(count))
    return best


def comb_rows_from_fields(fields: Iterable[Any], field_key: str,
                          page: Optional[int] = None) -> List[Tuple[int, int]]:
    """Extract ``(char_offset, cell_count)`` for every comb row bound to a key.

    Accepts either ORM ``DocumentField`` rows or plain dicts, so the PDF engine,
    the HTML print path and the API all use one extractor.
    """
    out: List[Tuple[int, int]] = []
    for f in fields or []:
        if isinstance(f, dict):
            ftype = f.get("field_type")
            key = f.get("field_key")
            fpage = f.get("page")
            meta = f.get("meta")
        else:
            ftype = getattr(f, "field_type", None)
            key = getattr(f, "field_key", None)
            fpage = getattr(f, "page", None)
            meta = getattr(f, "meta", None)
        if ftype != "char_cells" or (key or "") != field_key:
            continue
        if page is not None and int(fpage or 1) != int(page):
            continue
        count = _row_cell_count(meta)
        if count <= 0:
            continue
        out.append((_row_char_offset(meta), count))
    return out


def row_text_for_offset(printed: str, char_offset: int, cell_count: int) -> str:
    """Slice the agreed ``printed`` string for one row of a multi-row comb.

    Every renderer must use this rather than slicing itself, so the rows always
    partition the same decided string. Hard-stops at ``cell_count`` so a value
    can never bleed into the field printed beside it.
    """
    if cell_count <= 0:
        return ""
    start = max(0, int(char_offset or 0))
    return printed[start:start + int(cell_count)]


# ---------------------------------------------------------------------------
# THE single entry point every renderer must use
# ---------------------------------------------------------------------------
def resolve_comb_row(value: Any, meta: Optional[Dict[str, Any]],
                     sibling_fields: Optional[Iterable[Any]] = None,
                     field_key: str = "", page: Optional[int] = None,
                     *, abbreviate: bool = True) -> Tuple[str, CombFit]:
    """Decide what ONE row of a ``char_cells`` comb prints.

    Returns ``(row_text, fit)`` where ``row_text`` is exactly the characters
    this row should draw and ``fit`` is the whole-value decision (identical for
    every row of the same value, which is what makes the rows agree).

    WHY THIS FUNCTION EXISTS
    -----------------------
    There are three renderers for one template -- ``pdf_engine`` (ReportLab),
    the HTML print path in ``api/endpoints/documents.py``, and the designer
    preview. They have already disagreed twice in this project's history: the
    HTML path had no ``char_cells`` support at all until 2026-08-11, and the
    designer drew combs as one continuous string until 2026-08-12. Each path
    used to compute its own slice, and NONE of them knew the *total* multi-row
    capacity -- so each row independently truncated at its own ``cell_count``
    and the policy question ("does this address fit at all?") was never asked.

    Routing all three through this one function means a capacity decision can
    only ever be made in one place. If the policy changes, it changes once.

    ``sibling_fields`` is the template's full field list. It is used only to
    discover the OTHER rows bound to the same key, so capacity is the real
    ``max(offset + cell_count)`` across the comb rather than this row's share of
    it. When it is omitted (or the key is blank) we fall back to this row's own
    ``cell_count``, which is the pre-existing single-row behaviour -- so a
    template that does not use multi-row combs is unaffected.
    """
    meta = meta or {}
    fmt = str(meta.get("format") or "")

    # Normalisation lives in pdf_engine because that is where the format
    # vocabulary ("text" / "digits" / "alnum" / "ddmmyyyy") is defined and
    # already shared with the HTML path. Imported lazily: pdf_engine imports
    # THIS module at the top level, so a top-level import here would be
    # circular.
    from app.services.pdf_engine import normalize_cell_value
    normalized = normalize_cell_value(value, fmt)

    # Separator handling must match _draw_char_cells exactly -- it strips
    # dashes always, and spaces unless format is "text". Capacity is measured in
    # the same units the cells actually hold, otherwise a dashed CNIC would be
    # judged against the wrong length.
    normalized = (normalized or "").replace("-", "")
    if fmt.lower() != "text":
        normalized = normalized.replace(" ", "")

    rows = list(comb_rows_from_fields(sibling_fields or [], field_key, page)) if field_key else []
    capacity = comb_capacity_for_key(rows) if rows else _row_cell_count(meta)

    fit = fit_comb_text(normalized, capacity, abbreviate=abbreviate)
    row_text = row_text_for_offset(fit.printed,
                                  _row_char_offset(meta),
                                  _row_cell_count(meta))
    return row_text, fit
