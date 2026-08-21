# CSP Hardening — Option B (tracked future work, NOT a production blocker)

**Status:** Deferred / tracked
**Owner:** HussnainTechVertex Pvt Ltd
**Raised:** 2026-07-18 (CSP posture review)

## Current posture (production-ready)

The portal ships a **strong** Content-Security-Policy with exactly **one**
documented, reviewed exception:

| Directive      | Value                              | Strictness |
|----------------|------------------------------------|------------|
| `default-src`  | `'self'`                           | strict |
| `script-src`   | `'self' 'nonce-<per-request>'`     | **fully strict** — no `unsafe-inline`, no `unsafe-eval`, no `unsafe-hashes` |
| `style-src`    | `'self' 'unsafe-inline'`           | **one documented exception** |
| `font-src`     | `'self' data:`                     | strict |
| `img-src`      | `'self' data: blob:`               | strict |
| `connect-src`  | `'self'`                           | strict |
| `frame-ancestors` | `'self'` (prod) / `'self' + preview origins` (dev) | strict in prod |

`script-src` is where XSS risk actually lives, and it is fully closed:
inline `<script>` blocks carry the per-request nonce.

> **Update — Jinja2 eradication pass.** This section previously described
> `app/static/js/csp_rebinder.js`, which rebound the Jinja pages' inline
> `onclick=`/`onsubmit=` attributes to `addEventListener` so they would survive
> the nonce-only policy. The Jinja frontend and that shim have both been
> removed, so the file no longer ships. The **React SPA attaches every handler
> through `addEventListener`** (React's synthetic event system) and emits **no
> inline `on*=` attributes at all**, so the problem the rebinder solved cannot
> occur. `tests/test_csp_rebinder.py` was retargeted accordingly: it now proves
> the SPA source *and the built bundles* are eval-free, that the served shell
> carries no inline handlers, and that the rebinder is not reintroduced.

## Why `style-src 'unsafe-inline'` remains

The old justification ("TailwindCSS CDN JIT runtime") is **obsolete** — the
app is now 100% self-hosted and ships no Tailwind CDN. Inline `<style>`
**blocks** are already nonce-protected in every template, so they do **not**
depend on `'unsafe-inline'`.

The real reason: several JS-rendered HTML fragments are injected via
`innerHTML` with per-element inline `style="…"` **attributes** whose values
are computed at runtime. When the browser parses that markup, those inline
style attributes are governed by `style-src`; a strict
`style-src 'self' 'nonce-…'` would block them and break the widgets. CSP does
**not** cover styles applied via the CSSOM (`el.style.x = …`), only parsed
inline attributes and `<style>` tags.

### Known dynamic-inline-style sites (the refactor surface, ~13 templates)

- `app/templates/roles.html` — permission progress bar (`style="width:${pct}%"`)
- `app/templates/demand_detail.html` — fill bar (`style="width:${fillPct}%"`) + overlay preview
- `app/templates/portal_admin.html` — tenant feature bar (`style="width:${...}%"`)
- `app/templates/document_customize.html` — designer coordinate-overlay engine
  (absolute `left/top/width/height` per field), A4 canvas sizing
- `app/templates/protector_letter.html` — print/overlay positioning
- Plus modal/panel sizing (`max-height: calc(...)`, `min-height`) and a few
  static one-offs across `candidates.html`, `client_detail.html`,
  `dashboard.html`, `agent_cashbook.html`, `contact_support.html`,
  `login.html`, `forgot_password.html`, `base.html`.

## Option-B plan (do this in a dedicated hardening session)

1. **Static inline styles** → move to a nonced `<style>` block or a utility
   class. (Low risk.)
2. **Dynamic inline styles** → after inserting the fragment, set the computed
   value via the CSSOM: `el.style.width = pct + '%'` instead of baking
   `style="width:${pct}%"` into the injected HTML string. (This is the
   high-churn, regression-prone part — progress bars + designer overlay.)
3. **Flip the policy** in `app/core/csp_nonce.py`:
   `style-src 'self' 'unsafe-inline'` → `style-src 'self' 'nonce-{nonce}'`.
4. **Add a regression test** asserting `style-src` contains `'nonce-'` and does
   **not** contain `'unsafe-inline'` (mirror `test_csp_rebinder.py`'s
   `script-src` assertions).
5. **Re-run** the full pytest suite + a live browser smoke test of every page
   listed above (progress bars, modals, the document designer preview/PDF).

## Risk rationale for deferring

Style injection permits UI redressing but **not** code execution, and
`script-src` is already fully strict. The removal touches the exact
highest-churn UI areas (designer coordinate engine, progress widgets), so it
is treated as a separate, test-gated hardening phase rather than a release
blocker.

**Verdict:** Production-ready with one documented CSP exception
(`style-src 'unsafe-inline'`) — not a "fully strict CSP".
