# Audit Response — External Security Review, 2026-07-26

> **📁 ARCHIVED — point-in-time record.** This report describes the repository as it stood on **2026-07-26**.
> Paths, filenames, counts and version numbers in it are deliberately **not**
> refreshed as the code moves on, so the document stays an honest account of
> what was actually found and fixed then. Some assets it names have since been
> renamed or relocated. **For the current state of the system, read
> `PROJECT_DOCUMENTATION.md` and `README.md` at the project root — not this file.**


Response to the external audit of the Dogar Portal archive. The reviewer's
verdict was:

> "Fully clean production-ready, no — not until the stored-XSS surfaces are fixed
> and the remaining CSP exception is consciously accepted or refactored."

This document records what was done against each finding, what was found that
the audit did not report, and the one decision still owed by the project owner.

## Summary

| # | Finding | Severity | Status |
|---|---------|----------|--------|
| 1 | Stored XSS in HTML/JS templates | HIGH | ✅ Fixed — 62 sinks remediated, CI-enforced |
| 2 | CSP weakened with `style-src 'unsafe-inline'` | MEDIUM | ⚠️ Partly stale — narrowed to `style-src-attr`; **owner decision required** |
| 3 | Password reset gated on SMTP | LOW | ✅ Improved — boot warning, health field, actionable UX |
| 4 | **Account enumeration via reset status code** | **HIGH** | ✅ Fixed — **not in the audit**; found while working on #3 |

Verification: `scripts/scan_xss_sinks.py --check` → **0** findings;
`pytest` → **228 passed**; Playwright smoke of every remediated page with a live
stored payload → **0** JS page errors, payload inert.

---

## 0. Scope correction: the app has TWO frontends

Worth stating first, because it changes how finding #1 should be read.

`settings.USE_SPA` defaults to **`True`** (`app/core/config.py`). With that
default, page routes serve the **compiled React SPA** from `app/static/spa/` via
`_serve_spa_index()`. **The Jinja templates the audit cites only render when
`USE_SPA=false`.**

This is not a reason to dismiss the finding — the templates are live code, they
ship in the archive, and `USE_SPA=false` is a supported configuration — but it
does mean the cited line numbers were not the default-configuration attack
surface. An audit working from a static archive could not have known this.

Both frontends were therefore reviewed:

- **Jinja templates** — 62 unescaped sinks found and fixed (§1).
- **React SPA bundle** — audited separately; its two HTML-injection paths are
  safe **by construction**:
  - `dangerouslySetInnerHTML={{__html: wt(text)}}` — `wt()` escapes `&`, `<`,
    `>` **first**, then applies markdown-ish transforms (`**bold**` → `<b>`,
    `\n` → `<br>`). The order is the security property; reversing it would
    reintroduce the bug.
  - The document designer performs a `template.innerHTML` round-trip guarded by
    a sanitizer with a tag allow-list and an attribute allow-list that strips
    `on*` handlers and `javascript:`/`data:`/`vbscript:` URLs.

---

## 1. HIGH — Stored XSS (FIXED)

### Scope was ~15× larger than reported

The audit named 4 sinks. To avoid fixing only the examples, a static analyzer
(`scripts/scan_xss_sinks.py`) was built to enumerate every interpolation reaching
`innerHTML`/`outerHTML` across templates and static JS:

- **248** interpolations reaching an HTML sink
- **62** genuinely unescaped → all fixed
- **0** remaining

All four audit examples are included and fixed:

| Audit reference | Fix |
|---|---|
| `demand_detail.html:545-567` | `row()`/`gridCell()` now escape by default; explicit `isHtml=true` opt-out for pre-built markup |
| `client_detail.html:244-266` | `infoRow()`/`gridRow()` escape by default; `locParts` escaped |
| `clients.html:129-140` | row template escaped — **plus a worse bug the audit missed**: a `JSON.stringify()` into a single-quoted `on*` attribute, which allowed attribute breakout |
| `candidates.html:2424-2429` | OCR row rebuilt with `createElement` + `textContent` |

### Two sink classes — and why the second nearly slipped through

1. **Template literals** — `el.innerHTML = \`<td>${v}</td>\`` — 47 sinks.
2. **String concatenation** — `el.innerHTML = '<span>' + v` — 15 sinks.

Class 2 was invisible to the first analyzer, which only parsed template
literals. It surfaced only after a concatenation detector was added. The lesson
was applied to the tests: **each pass now has its own mutation probe**, because a
probe that mutates only a template literal would still pass if the concatenation
pass were deleted — exactly the blind spot that hid these 15 sinks.

### Strategy: escape per interpolation, do NOT sanitize whole trees

`safe_dom.js`'s sanitizer strips `on*` handlers, but the templates **depend** on
inline `onclick`, which `csp_rebinder.js` rebinds to `addEventListener` (with a
`MutationObserver` for dynamically-added nodes) to satisfy the strict
`script-src`. Routing markup through `element.safeHTML` would have stripped the
handlers and **broken the app**. Escaping at each interpolation site preserves
behaviour while closing the hole.

### `esc()` vs `escJs()` — a correctness trap, now test-enforced

A value inside a single-quoted JS string in an `on*=` handler **must** use
`escJs()`, not `esc()`. `esc()` emits `&#39;` for an apostrophe, and
`csp_rebinder.js`'s deliberately eval-free tokenizer cannot decode HTML
entities. Using `esc()` there is both a security hole and a **functional break**:
a client named `O'Brien & Co` would break the buttons in its own table row.

Enforced by `test_no_escaped_html_entities_inside_inline_handler_strings`, plus a
companion test forbidding `JSON.stringify` inside a single-quoted handler.

### Escapers centralized

`esc`, `escAttr`, `escUrl`, `escJs` are defined **once**, in
`app/static/js/safe_dom.js`. Per-page duplicates were removed and are blocked by
a test, so a page cannot shadow the audited implementation with a weaker one.

Three unauthenticated pages (`forgot_password`, `reset_password`,
`contact_support`) do **not** load `safe_dom.js`. Rather than pull the helper in
just for an error banner, those were fixed **structurally**: static icon markup
via `innerHTML`, then the server-supplied message written through `textContent`.
Safe by construction, no helper needed.

### A fatal regression found and fixed along the way

While remediating, `/roles` and `/portal-admin` were discovered to be **entirely
dead**. Root cause: `app.js` declares a top-level `const escapeHtml` and loads at
`base.html:1144`, **before** `{% block scripts %}` at `base.html:1813`. A page
redeclaring `const escapeHtml` threw
`Identifier 'escapeHtml' has already been declared` — a **SyntaxError**, which
aborts the *entire* script block and silently kills the page.

Fixed with a collision-safe guard, and locked by
`test_page_scripts_do_not_redeclare_app_js_globals`. The test covers
`const`/`let`/`class` but deliberately **not** `function`, because duplicate
function declarations are legal and four pages intentionally override
`closeModal` — verified empirically with `node --check` rather than assumed.

### Verified at runtime, not just statically

A static "0 findings" only proves the analyzer is quiet. So:

1. A real payload — `<img src=x onerror=window.__XSS_FIRED=1>O'Brien & Co`,
   plus `<svg onload>`, `<script>` and `<b onmouseover>` variants — was stored
   through the API into a real client record.
2. All 13 remediated pages were loaded under Playwright with `USE_SPA=false`.

Results: **nothing executed**, **no live payload DOM nodes**, payload rendered as
**literal text**, the apostrophe survived with **no `&#39;` artefact** (proving no
double-escaping), inline handlers still showed as `(rebound)`, the edit modal
still opened, and there were **zero** JS page errors on every page.

The 8 concatenation sinks only fire on an error path, so they were additionally
driven with a **hostile stubbed API response** — proving the *sink* is safe,
rather than merely that today's server text happens to be harmless. The one
intentional-markup sink (`setOk`) was confirmed to still render its `<b>`
emphasis, guarding against an over-escaping regression.

The payload record was deleted and `.env` restored afterwards.

### CI enforcement

```bash
python3 scripts/scan_xss_sinks.py           # human report
python3 scripts/scan_xss_sinks.py --check   # CI mode, non-zero on findings
python3 -m pytest tests/test_xss_sinks.py   # 8 tests
```

The 8 tests cover: the zero-findings ratchet (with a remediation cheat-sheet in
the failure message), **two** mutation probes (one per sink class), escaper
centralization, the `escJs`-in-handler rule, the `JSON.stringify`-in-handler
rule, and the `app.js` global-collision guard.

Suppression is possible but deliberately awkward: a per-line `xss-sink-ok` pragma
with a justification, or a reviewed entry in `TRUSTED_MARKUP_VARS_BY_FILE`. The
pragma is greppable **by design**, so waivers cannot quietly accumulate the way a
name allow-list can.

---

## 2. MEDIUM — CSP `style-src 'unsafe-inline'` (PARTLY STALE — decision required)

The finding describes `style-src 'unsafe-inline'`. That directive is still
present, but is now only a **legacy fallback**. Live header, verified
2026-07-26:

```
default-src 'self'
script-src      'self' 'nonce-<per-request>'    ← strict
style-src-elem  'self' 'nonce-<per-request>'    ← strict: no inline <style>
style-src-attr  'unsafe-inline'                 ← the actual remaining gap
style-src       'self' 'unsafe-inline'          ← ignored by modern browsers
font-src 'self' data: · img-src 'self' data: blob: · connect-src 'self'
frame-ancestors 'self'
```

Modern browsers honour the more specific `-elem`/`-attr` directives and ignore
the broad `style-src` fallback, which exists only for browsers that don't
understand the split. **No code change is needed** — the hardening the finding
asks for has largely already happened.

Residual exposure is `style-src-attr` only: runtime-computed inline `style="…"`
**attributes** (progress-bar fills, modal sizing, the document-designer
coordinate overlay, React `style={{…}}` props). A style *attribute* cannot
execute script, so the worst case is **UI redressing** — visual spoofing — not
code execution. Combined with strict `script-src` and `frame-ancestors 'self'`,
this is a hardening gap rather than an exploitable path to script execution.

### 🔴 Decision required from the project owner

The audit's acceptance criteria require this to be *"consciously accepted or
refactored."* That is a judgement call, not a technical one:

- **Accept** — no work, no regression risk. Leaves a UI-redressing-only gap.
- **Refactor** — replace every runtime `style="…"` with utility classes or CSS
  custom properties applied via `classList` / `style.setProperty()`. Mechanical
  but broad: progress bars and modal sizing are easy; the document designer's
  coordinate overlay is inherently per-pixel and is the hard case.

**Recommendation: accept for this release and schedule the refactor.** The
residual risk is not code execution, and the change surface is wide enough to
carry its own regression risk — which would be a poor trade against a
UI-redressing-only gap. Tracked in
[`docs/CSP_HARDENING_OPTION_B.md`](docs/CSP_HARDENING_OPTION_B.md).

---

## 3. LOW — Password reset gated on SMTP (IMPROVED)

The audit is right that this is a production-readiness dependency rather than a
security flaw. The endpoint's core logic was already correct and was left
intact:

- It fails **closed** (`503`) instead of falsely reporting that a link was sent.
- The config check runs **before** the user lookup, so existent and non-existent
  addresses behave identically — no enumeration from that path. (The existing
  code already carried a comment explaining this ordering was deliberate.)

The real gaps were operational: the only signal was a WARNING logged the *first
time a locked-out user tried to reset* — i.e. discovered by an end user, not by
the operator. Closed on three fronts:

1. **Boot-time WARNING** (`Settings.assert_production_safe`) naming the exact env
   vars and the fallback (admin-driven reset from the Users screen).
   Deliberately **not** a hard failure: plenty of deployments legitimately reset
   passwords out-of-band, and refusing to boot over an optional feature would be
   worse than the gap it closes.
2. **`GET /api/health` reports `password_reset`** — `available` or
   `unavailable_smtp_not_configured` — so uptime monitoring catches it. It is a
   **capability** flag: an optional feature never marks the service unhealthy,
   only the database does that.
3. **Actionable 503 UX** on `/forgot-password`: explains that retrying will not
   help, points to the administrator and to support, and **disables the submit
   button** rather than inviting a pointless retry loop against a permanently
   unavailable endpoint.

---

## 4. HIGH — Account enumeration on password reset (FIXED — not in the audit)

Found while implementing #3, and more serious than the finding that led to it.

`/api/auth/forgot-password` used to raise **500** when the SMTP send threw. That
code path is reachable **only after matching an active user**, so the status code
alone was a reliable account-existence oracle:

```
registered address    -> send attempted -> throws -> 500
unregistered address  -> early return             -> 200
```

No timing analysis required — just read the status code.

**Why this was likely to be live, not theoretical:** `.env.example` ships
*placeholder* SMTP values (`smtp.yourprovider.com`, `noreply@yourcompany.com`).
These are non-empty, so the naive `if SMTP_HOST and SMTP_USER` config gate
treated email as configured, the app attempted a real send, and the send failed
to authenticate. Any deployment that copied `.env.example` and filled in
everything *except* SMTP landed squarely in the leaking branch.

Confirmed against the running instance before fixing:

```
POST /api/auth/forgot-password  {"email":"admin@yourcompany.com"}          -> 500
POST /api/auth/forgot-password  {"email":"definitely-not-a-user@..."}      -> 200
```

### Fix, on both axes

**Response symmetry.** A delivery failure is a server-side fault that the
requester can neither cause nor fix, and it says nothing about the address they
typed — so it must not change what they see. All branches now return the same
account-neutral `200`, and the failure is escalated where an operator will act on
it (ERROR log + `/api/health` + boot warning). The unused token row is
single-use and hash-stored, so it is inert and simply expires after 30 minutes.

**Root cause.** `settings.email_configured` is now the single source of truth for
"can this server send mail?", used by all three call sites, and it **rejects the
`.env.example` placeholders**. So a placeholder config now produces the honest,
symmetric `503` up-front instead of failing later inside the send.

After the fix, verified live — identical status **and** identical body:

```
registered   -> 503  "Password reset email cannot be sent because email delivery is not configured…"
unregistered -> 503  "Password reset email cannot be sent because email delivery is not configured…"
/api/health  -> "password_reset": "unavailable_smtp_not_configured"
```

### Regression tests (mutation-verified)

Added to `tests/test_auth.py`:

- `test_forgot_password_no_enumeration_when_smtp_send_fails` — stubs `smtplib`
  to raise, asserts identical status **and** body for registered vs unregistered.
- `test_forgot_password_placeholder_smtp_treated_as_unconfigured`
- `test_email_configured_property` — including whitespace-only and
  case-insensitive placeholder handling.
- `test_health_reports_password_reset_capability` — including that an
  unavailable optional feature still returns HTTP 200.

The first test was **confirmed to fail against the old code** (reproducing
`500 vs 200`) before the fix was restored — so it genuinely bites.

> Note: the pre-existing enumeration tests hardcode `admin@yourcompany.com`,
> which is **not** the seeded account in the test environment
> (`conftest.py` seeds `admin@test.local`). They were therefore comparing two
> *non-existent* addresses and could not have caught this class of bug. The new
> tests use the real seeded credentials fixture.

---

## Verification

```
scripts/scan_xss_sinks.py --check   ->  0 findings, exit 0
python3 -m pytest -q                ->  228 passed
Playwright smoke (USE_SPA=false)    ->  13/13 pages pass, 0 JS pageerrors
Concatenation-sink probes           ->  8/8 sinks proven safe vs hostile input
Live enumeration check              ->  symmetric 503 for both address classes
503 UX check                        ->  next step offered, retry loop stopped
```

## Remaining owner action

1. **CSP `style-src-attr`** — accept or schedule the refactor (§2).
   Recommendation: accept now, schedule later.
2. **Configure SMTP** before relying on self-service password reset, or
   document that resets are admin-driven via the Users screen (§3).
