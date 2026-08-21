"""The Python side of the tenant-branding contract.

WHY THIS MODULE EXISTS (2026-08-19)
===================================
The operator asked for topbar and DOCUMENT branding to follow the active office
automatically, naming logo, colours and fonts. Those three values are now
consumed by two completely separate renderers:

  * the SPA shell, in TypeScript (app/frontend/src/lib/branding.ts), and
  * the server-rendered document/letterhead path, in Python.

That is exactly the shape of the defect this project has already been bitten by
in the coordinate engine: the same constant authored twice, in two languages,
free to drift, with nothing failing when they disagree. The document would then
print in one typeface while the screen showed another -- and the ONLY symptom
would be a document that looks subtly wrong, which nobody can grep for.

So the allow-list lives here once, and tests/test_tenant_font_branding.py parses
the TypeScript source and asserts the two sets are identical. Neither side is
allowed to gain or lose a font without the other.

WHY AN ALLOW-LIST OF KEYS AT ALL
--------------------------------
The stored value is written into a live CSS custom property (``--brand-font``)
and into the document letterhead's inline style. CSS cannot execute script, but
an unvalidated value is still:

  * a rule-termination surface -- a stored ``}`` ends the rule early and breaks
    the surrounding stylesheet, so one cosmetic field can wreck the whole
    shell's typography; and
  * a way for a legacy or hand-edited row to produce an invalid declaration
    that renders as *nothing*, i.e. an office whose branding silently fails.

A key can only ever select a stack that both renderers already vouch for.

DOGAR IS NOT AFFECTED BY ANY OF THIS
------------------------------------
Nothing here applies to the root/control portal, which has no tenant row: the
branding path is a deliberate no-op without a resolved tenant and Dogar keeps
the typography authored in its own stylesheets. Preserving that is a hard
requirement of the brief, and it is enforced by the CSS fallback
``var(--brand-font, var(--font-body))`` rather than by remembering not to touch
it.
"""

from __future__ import annotations

import contextvars
from typing import Any, Final

#: The default key. NULL and "" both resolve to this.
_DEFAULT_FONT_KEY: Final[str] = "system"
DEFAULT_FONT_KEY: Final[str] = _DEFAULT_FONT_KEY

#: Every typeface key a tenant may store.
#:
#: MUST stay identical to the keys of FONT_STACKS in
#: app/frontend/src/lib/branding.ts -- checked by
#: tests/test_tenant_font_branding.py, which parses that file.
ALLOWED_FONT_KEYS: Final[frozenset[str]] = frozenset(
    {"system", "inter", "roboto", "lato", "serif", "mono"}
)

#: Server-side resolution of a key to a real ``font-family`` declaration, for
#: the document/letterhead renderer. Values are intentionally NOT identical to
#: the SPA's stacks: a printed document cannot rely on the reader's system UI
#: font, so each stack ends in a generic family the PDF/print path can actually
#: resolve. The KEYS are what must match across the two languages, not the
#: stacks -- see the test module for why that distinction is deliberate.
FONT_STACKS_PRINT: Final[dict[str, str]] = {
    "system": "Inter, 'Helvetica Neue', Helvetica, Arial, sans-serif",
    "inter": "Inter, 'Helvetica Neue', Helvetica, Arial, sans-serif",
    "roboto": "Roboto, 'Helvetica Neue', Helvetica, Arial, sans-serif",
    "lato": "Lato, 'Helvetica Neue', Helvetica, Arial, sans-serif",
    "serif": "Georgia, 'Times New Roman', Times, serif",
    "mono": "'DejaVu Sans Mono', 'Courier New', Courier, monospace",
}


def normalise_font_key(value: str | None) -> str:
    """Fold any stored value to a key that is safe to render.

    Never raises and never returns something unknown: a legacy row holding
    ``"Comic Sans"``, ``None`` or ``"}"`` all resolve to the default. Rejecting
    bad input belongs at the API boundary (a 422 the admin can see); by the time
    a value is being RENDERED, refusing to draw would mean an office with no
    typography at all, which is worse than the default.
    """
    key = str(value or "").strip().lower()
    return key if key in ALLOWED_FONT_KEYS else _DEFAULT_FONT_KEY


def print_font_stack(value: str | None) -> str:
    """The ``font-family`` declaration for a document rendered for this tenant."""
    return FONT_STACKS_PRINT[normalise_font_key(value)]


# ---------------------------------------------------------------------------
# Request-scoped active tenant, for renderers that cannot reach the request
# ---------------------------------------------------------------------------
# WHY A CONTEXTVAR AND NOT A PARAMETER (measured 2026-08-19)
#
# The document renderer in app/services/letterhead_renderer.py wraps every
# letter through `_wrap_document()`, which has THIRTEEN call sites across
# candidates.py, demands.py, protector_letters.py and the renderer itself. Of
# those, exactly ONE (render_noc_verification) ever received a `tenant=`
# argument -- so twelve document types physically could not know which office
# they were being printed for, no matter what the tenants table held.
#
# Threading `tenant` through thirteen signatures and every caller would be the
# other option, and it is worse in the way that matters here: it is thirteen
# opportunities to forget one, and a forgotten one is INVISIBLE -- the document
# still renders, in Dogar's branding, on a partner office's letterhead. There is
# no error and no wrong pixel to notice, which is exactly how the font path
# stayed dead for as long as it did.
#
# The CSP-nonce path in app/core/csp_nonce.py already solved this same problem
# the same way for the same renderers, and the note there records why: these
# helpers are called deep inside render functions whose callers have no reason
# to know about request state. This follows that established pattern rather than
# inventing a second one.
#
# Read-only from the renderer's point of view: the middleware publishes, the
# renderers consume, and outside a request the getter returns None so
# direct-render unit tests keep working and fall back to Dogar's own branding.
current_tenant: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "current_tenant_branding", default=None
)


def get_current_tenant() -> Any:
    """The tenant resolved for this request, or None on the Dogar root portal.

    Safe to call from any renderer without knowing whether a request is active.
    None is the correct answer for the control portal, and it is what keeps the
    Dogar branding untouched: every consumer treats None as "use the authored
    defaults" rather than as an error.
    """
    try:
        return current_tenant.get()
    except LookupError:
        return None


def document_font_stack(tenant: Any = None) -> str:
    """``font-family`` for a document, for an explicit tenant or the active one.

    An explicitly-passed tenant always wins, so the one call site that already
    threads `tenant=` keeps working unchanged and remains testable without a
    request context.
    """
    t = tenant if tenant is not None else get_current_tenant()
    if t is None:
        # No tenant: Dogar. Return the SERIF stack the letterhead has always
        # used, not the "system" key's sans stack -- switching the root portal's
        # documents to a different typeface is precisely what the brief forbids.
        return DOGAR_DOCUMENT_FONT
    return print_font_stack(getattr(t, "font_key", None))


#: The typeface Dogar's documents have always been rendered in, kept as its own
#: constant so `document_font_stack(None)` provably returns the AUTHORED value
#: rather than whatever the tenant default happens to be. Matches the
#: `html, body` rule in letterhead_renderer._BASE_STYLES.
DOGAR_DOCUMENT_FONT: Final[str] = '"Times New Roman", Times, serif'
