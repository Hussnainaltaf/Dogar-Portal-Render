"""CSP Nonce middleware — per-request nonce for inline scripts/styles.

Uses cryptographically random nonces that change on every request for
script-src. All inline <script> blocks in templates must include
nonce attribute.

──────────────────────────────────────────────────────────────────────────
CSP posture (reviewed 2026-07-18): STRONG, with ONE documented exception.
──────────────────────────────────────────────────────────────────────────
* script-src: FULLY STRICT — `'self' 'nonce-…'` only. No 'unsafe-inline',
  no 'unsafe-eval', no 'unsafe-hashes'. Inline `<script>` blocks carry the
  per-request nonce, and legacy inline event-handler attributes
  (onclick=/onsubmit=/…) are rebound to addEventListener at load time by the
  self-hosted scripts with nonce attributes. This is where XSS risk
  actually lives, and it is closed.

* style-src (HARDENED 2026-07-20): 'unsafe-inline' is no longer applied
  blanket. The policy now uses the granular CSP3 directives:

    - style-src-elem 'self' 'nonce-…'  → STRICT. <style> blocks and
      <link rel=stylesheet> only load if self-hosted or nonce'd. An injected
      <style>…</style> (the actual CSP style-injection attack surface) is now
      BLOCKED. Every inline <style> in the SPA build already carries the
      per-request nonce, so nothing legitimate breaks.
    - style-src-attr 'unsafe-inline'   → inline style="…" ATTRIBUTES only.
      This exception is deliberately shell-only and compatibility-limited.
      Runtime-computed template values such as progress-bar widths and tenant
      colours are no longer injected as `style="..."` HTML; they are applied via
      CSSOM (`el.style.*`) after safe DOM insertion. The remaining allowance is
      for sanitized safeHTML content and React/CSSOM-driven
      UI surfaces that still materialize style attributes in the live DOM. CSP has
      no nonce for style attributes, so this cannot be replaced by a nonce. It is
      permitted here, but a style ATTRIBUTE cannot execute script — worst case is
      UI redressing, never code execution.
    - style-src 'self' 'unsafe-inline' → kept ONLY as the legacy-browser
      fallback (browsers that ignore *-attr/*-elem). Modern browsers use the
      stricter split above and ignore this fallback.

  Net effect: the exploitable half of the old exception (injected <style>
  elements) is now closed; only inert style attributes retain inline. Combined
  with the fully strict script-src, both real code-execution surfaces are shut.

  Verdict: PRODUCTION-READY. style-src-elem strict; 'unsafe-inline' scoped to
  inert style attributes + a legacy fallback.

──────────────────────────────────────────────────────────────────────────
ZERO-EXCEPTION MODE (added 2026-08-04): the style policy is PER-RESPONSE.
──────────────────────────────────────────────────────────────────────────
The exception above is real but not universal. CSP offers no nonce mechanism
for style attributes — a nonce attaches to an ELEMENT, and `style="…"` is not
one — so `'unsafe-inline'` can never be *replaced* by a nonce, only DELETED,
which means the attributes themselves have to go.

For the *standalone document renders* that is achievable: they are produced
server-side in a single pass, with no runtime DOM building, so every style
attribute is known at render time. `app/core/style_hoist.py` rewrites them into
a nonced <style> element behind a `data-csp-h` hook, then calls
`mark_styles_hoisted()` below. When that flag is set for the current request,
this middleware emits:

    style-src-attr 'none'                    ← ZERO exceptions
    style-src      'self' 'nonce-…'          ← fallback, also strict

Currently opted in: letterhead/undertaking documents, receipts, the designer
print preview, and the candidate profile print.

The React SPA shell keeps `'unsafe-inline'` and genuinely need it,
but the scope is intentionally narrow: dynamic progress widths, tenant colours
and toast/dialog layout formerly injected as HTML `style="..."` attributes have
been moved to CSS classes or CSSOM updates. The residual exception covers only
sanitized safeHTML content and runtime UI engines whose
style attributes are created after the server response. Tightening the shell
would risk breaking those surfaces, so the flag defaults to False and the
zero-exception mode remains strictly opt-in, per response.

WHY THE FLAG IS A MUTABLE DICT AND NOT A PLAIN ContextVar
---------------------------------------------------------
Starlette's BaseHTTPMiddleware runs the downstream app in a CHILD Context. A
`ContextVar.set()` performed inside a route is therefore INVISIBLE to the
middleware that wrapped it — the child's assignments do not propagate upward.
Mutating an object the middleware already holds a reference to *does* cross that
boundary. This was established by probe, not assumed, and the guard is
mutation-verified: swapping the dict for a naive `set()` makes both tests in
tests/test_csp_zero_exception.py fail.
"""
from __future__ import annotations

# Cross-origin parents allowed to embed the app in an iframe when NOT in
# production (hosted sandbox previews live on a different origin
# than the app, so `frame-ancestors 'self'` alone would refuse them). Production
# keeps the strict `frame-ancestors 'self'` — no external framing.
import contextvars
import os as _os
import secrets

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings

# Per-request nonce accessible from anywhere in the request lifecycle
# WITHOUT having to thread `request` through every function signature.
# This is the escape hatch used by the letterhead / receipt renderers
# so they can put `nonce="…"` on their <style>/<script> blocks without
# every one of their many callers having to pass the request through.
#
# Do NOT read this from module-import time — only from inside a request
# handler (or code called from one). Outside a request the getter returns
# an empty string, which safely falls back to a bare (non-nonced) tag; in
# that case there is no CSP header being applied either (e.g. unit tests
# rendering HTML directly), so it does not matter.
current_csp_nonce: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_csp_nonce", default=""
)


def get_current_csp_nonce() -> str:
    """Return the CSP nonce for the current request, or "" if none is active.

    Safe to call from any renderer without worrying about whether we are
    inside a request context — outside a request we simply return "" and
    the caller emits a bare tag (no CSP header being enforced either).
    """
    try:
        return current_csp_nonce.get() or ""
    except LookupError:
        return ""


# ── Per-response CSP hardening signal ────────────────────────────────────────
# A MUTABLE container, deliberately. The renderers that hoist their inline
# style attributes (see app/core/style_hoist.py) run *inside* call_next, and a
# plain `ContextVar.set()` there does NOT propagate back out to the middleware
# — Starlette's BaseHTTPMiddleware runs the downstream app in a child Context,
# so child assignments are invisible to the parent. Verified empirically, not
# assumed. Mutating an object the middleware already holds a reference to does
# cross that boundary, so the flag lives in a dict.
_csp_flags: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "csp_flags", default=None
)


def mark_styles_hoisted() -> None:
    """Declare that this response contains NO inline style attributes.

    Called by ``hoist_inline_styles()`` once it has rewritten a document.
    Lets the middleware drop ``style-src-attr`` to ``'none'`` for *this
    response only*, instead of weakening the policy for the whole site.

    Fails silently outside a request (unit tests render HTML directly).
    """
    try:
        flags = _csp_flags.get()
        if flags is not None:
            flags["styles_hoisted"] = True
    except LookupError:  # pragma: no cover - defensive
        pass

_PREVIEW_FRAME_ANCESTORS = (
    "https://*.sandbox.novita.ai https://*.e2b.dev "
    + _os.environ.get("EXTRA_FRAME_ANCESTORS", "")
).strip()

# AUDIT FIX (2026-07-06): the app is now 100% self-hosted — every stylesheet,
# script, icon font and web font ships from /static/vendor/ (verified: no
# template or the SPA index.html references any external CDN). The former CDN
# allowlists (cdn.tailwindcss.com, jsdelivr, cdnjs, unpkg, jquery, datatables,
# fonts.googleapis/gstatic) are therefore removed so the CSP collapses to a
# strict `'self'` policy. This closes the supply-chain risk of a compromised
# third-party CDN injecting script into a portal that handles passport / CNIC
# PII. Re-add an origin here ONLY if you deliberately reintroduce a CDN asset.
_CDN_SCRIPT_ORIGINS = ""
_CDN_STYLE_ORIGINS = ""
_CDN_FONT_ORIGINS = ""
_CDN_IMG_ORIGINS = ""


class CspNonceMiddleware(BaseHTTPMiddleware):
    """Generate a fresh CSP nonce per request and inject into the header.

    The nonce is stored in ``request.state.csp_nonce`` for access by the
    SPA shell and any server-rendered HTML (e.g. print documents).
    """

    async def dispatch(self, request: Request, call_next):
        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce
        # Also publish through a ContextVar so renderers deep inside the
        # call graph (letterhead_renderer._wrap_document, receipts_renderer._wrap)
        # can pick up the nonce without needing every one of their callers
        # to forward `request` explicitly. Reset the token in `finally` so
        # the value never leaks to another request in the same worker.
        token = current_csp_nonce.set(nonce)
        # Fresh per-request flag container. The hoister mutates this dict from
        # inside call_next to tell us the response has no style attributes left
        # (see mark_styles_hoisted for why it must be a mutable container).
        flags: dict = {}
        flags_token = _csp_flags.set(flags)
        try:
            response = await call_next(request)
        finally:
            current_csp_nonce.reset(token)
            _csp_flags.reset(flags_token)
        styles_hoisted = bool(flags.get("styles_hoisted"))
        # Only inject nonce-based CSP for HTML responses (skip API JSON)
        content_type = response.headers.get("content-type", "")
        if "text/html" in content_type:
            # script-src: nonce-protected (strong — only nonce'd scripts run)
            # style-src:  HARDENED — 'unsafe-inline' is scoped to inert style
            #   ATTRIBUTES via style-src-attr; <style>/<link> are strict via
            #   style-src-elem 'self' 'nonce-…' (injected <style> now blocked).
            #   Plain style-src is kept only as a legacy-browser fallback.
            #   See module docstring for the full rationale.
            # frame-ancestors: strict 'self' in production; in dev/preview also
            # allow the known preview parents so the app renders inside the
            # hosted preview iframe.
            if settings.is_production:
                frame_ancestors = "'self'"
            else:
                frame_ancestors = f"'self' {_PREVIEW_FRAME_ANCESTORS}"
            # Build each directive, appending optional CDN allowlists only when
            # non-empty so the policy stays clean (`'self'`-only after the audit).
            def _dir(base: str, extra: str) -> str:
                extra = (extra or "").strip()
                return f"{base} {extra}".strip() if extra else base
            # style-src hardening (2026-07-20): split the blanket
            # `style-src 'unsafe-inline'` into the granular CSP3 directives so
            # 'unsafe-inline' is scoped to harmless style ATTRIBUTES only:
            #
            #   * style-src-elem 'self' 'nonce-…'  → <style> blocks and
            #     <link rel=stylesheet> are now STRICT: only self-hosted
            #     stylesheets and nonce'd inline <style> load. An injected
            #     <style>…</style> (the real CSP style-injection surface) is
            #     now BLOCKED. Every inline <style> in the SPA build already
            #     carries {{ csp_nonce }}, so this does not break us.
            #   * style-src-attr 'unsafe-inline'   → inline style="…" ATTRIBUTES
            #     for the shell only. Dynamic progress widths / tenant colours are
            #     applied with CSSOM, not injected style attributes. The residual
            #     allowance covers legacy sanitized fragments and runtime UI engines
            #     whose live DOM styles cannot be nonced. Style attributes cannot
            #     execute script, so this permits at most UI redressing, not code
            #     execution.
            #   * style-src 'self' 'unsafe-inline' → retained ONLY as the
            #     fallback for legacy browsers that ignore the *-attr/*-elem
            #     directives; modern browsers use the stricter split above.
            style_elem = _dir(f"style-src-elem 'self' 'nonce-{nonce}'", _CDN_STYLE_ORIGINS)
            # ── Zero-exception CSP for hoisted responses (2026-08-04) ─────────
            # When app/core/style_hoist.py has rewritten every inline style
            # ATTRIBUTE in this response into rules inside a NONCED <style>
            # element, nothing legitimate needs style-src-attr any more, so it
            # drops to 'none' and this response carries a CSP with NO
            # 'unsafe-inline' anywhere at all.
            #
            # This is decided per RESPONSE, not globally, and that is load
            # bearing: the standalone print/preview documents are entirely
            # self-contained (they load no external JS), whereas the app
            # shell loads /static/js/safe_dom.js, which legitimately calls
            # setAttribute('style', …) at runtime. CSP checks style ATTRIBUTES
            # when they are written, so tightening the shell would break it —
            # the hoister only sees server-rendered HTML, never runtime writes.
            if styles_hoisted:
                style_attr = "style-src-attr 'none'"
                # The legacy fallback must be tightened in step, otherwise a
                # browser that ignores the *-attr/*-elem split still gets
                # 'unsafe-inline' and the hardening is cosmetic on exactly the
                # old browsers that need it most.
                style_fallback = _dir(
                    f"style-src 'self' 'nonce-{nonce}'", _CDN_STYLE_ORIGINS
                )
            else:
                style_attr = "style-src-attr 'unsafe-inline'"
                style_fallback = _dir(
                    "style-src 'self' 'unsafe-inline'", _CDN_STYLE_ORIGINS
                )
            csp = "; ".join([
                "default-src 'self'",
                _dir(f"script-src 'self' 'nonce-{nonce}'", _CDN_SCRIPT_ORIGINS),
                style_fallback,
                style_elem,
                style_attr,
                _dir("font-src 'self' data:", _CDN_FONT_ORIGINS),
                _dir("img-src 'self' data: blob:", _CDN_IMG_ORIGINS),
                "connect-src 'self'",
                f"frame-ancestors {frame_ancestors}",
                # ── Nonce-bypass hardening (added 2026-07-28) ────────────────
                # These three were MISSING, which left documented bypasses of
                # the nonce policy above open:
                #
                #   object-src 'none'  — <object>/<embed> can execute active
                #     content (Flash/PDF/SVG plugin contexts) WITHOUT being
                #     covered by script-src. 'none' is the standard hardening
                #     and this app never embeds plugins.
                #
                #   base-uri 'self'    — an injected <base href="//attacker">
                #     re-roots every RELATIVE script/style URL on the page. A
                #     nonce policy that trusts 'self' can be turned against
                #     itself this way, because the browser resolves 'self'
                #     relative to the (attacker-controlled) base. This is the
                #     most important of the three.
                #
                #   form-action 'self' — stops an injected <form action=...>
                #     (or a hijacked one) from POSTing credentials/CSRF tokens
                #     to a third-party origin.
                #
                # Verified by tests/test_csp_policy.py.
                "object-src 'none'",
                "base-uri 'self'",
                "form-action 'self'",
            ]) + ";"
            response.headers["Content-Security-Policy"] = csp
        return response
