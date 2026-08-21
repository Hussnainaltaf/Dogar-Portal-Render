# Dogar Trading Corporation Portal — deployment package

Built from the current audited `main` workspace. Backend suite **1120 passed / 0 failed**
(1113 collected; 2 skipped, both for stated data-dependency reasons, not failures:
`test_fresh_install_geometry` skips its snapshot-staleness check because the local
`data/` database was written after the shipped geometry snapshot was committed, so a
mismatch could not distinguish a stale snapshot from locally edited template geometry;
`test_status_stream_tables` skips one case when no candidate/job_category rows exist
to build an assignment from. The suite is run via `bash scripts/run_suite_chunked.sh`,
which uses a fresh interpreter per chunk — a single `pytest tests/` process is killed
by the OOM killer partway through this sandbox's 985 MB ceiling.)
`ruff` clean; frontend `tsc -b` + `vite build` clean.

The compiled React SPA is already included in `app/static/spa/`, so the app runs
without any Node/npm step. You only need Node if you intend to change the
front-end source in `app/frontend/`.

---

## 1. What is and is not in this archive

| Included | Note |
|---|---|
| Full Python backend, Alembic migrations, tests | |
| Compiled SPA (`app/static/spa/`) | ready to serve, no build needed |
| Front-end source (`app/frontend/src`) | `node_modules` excluded — run `npm install` only if you rebuild |
| Background scans (`app/static/pdf_backgrounds/`) | required by the Overlay Engine |
| `.env.example` | template for configuration |
| `Dockerfile`, `docker-compose.yml` | container path |
| `.git/` | a **fresh, single-commit** repository, so you have version control from day one without inheriting internal development history |

**Deliberately excluded:** `.env` (holds your `SECRET_KEY`, admin password and
SMTP credentials — never ship it inside a distributable archive), `node_modules/`,
`__pycache__/`, logs, and **`data/` in its entirety**.

### ⚠️ This archive contains NO database — read this before deploying

The earlier v1.1.0 package bundled `data/dogar_trading.db` (7.9 MB of real
candidate, client and passport records) plus two demo tenant databases. An
external audit flagged that, correctly: a distributable archive that carries
live personal data can only be transferred over a private channel, can never be
put in a repository or a shared drive, and quietly leaks two demo tenants nobody
asked for.

This package is therefore built by `scripts/make_release.sh`, which exports
**tracked files only** via `git archive`. `data/` is gitignored, so live data
cannot leak by construction — not by an exclude list that someone has to keep in
sync. The build then self-audits and refuses to produce an archive containing
any `*.db`, `*.db-wal`, `*.db-shm`, `.env`, `data/` or `logs/`.

**What that means for you — pick one:**

* **New installation.** Do nothing extra. `alembic upgrade head` creates the
  schema and first boot seeds the bootstrap admin from `DEFAULT_ADMIN_PASSWORD`.

* **Migrating an existing portal (you have live records).** Deploy this archive,
  then copy your database in *before* first boot:

  ```bash
  mkdir -p data
  cp /secure/transfer/dogar_trading.db data/
  # per-tenant portals, if you use them:
  mkdir -p data/tenants && cp /secure/transfer/tenant_*.db data/tenants/
  alembic upgrade head        # idempotent; safe against an existing database
  ```

  Transfer the `.db` files over a private channel — **not** inside this archive.
  Migrations are idempotent, so running them against your existing database is
  safe and will bring it up to the current schema.

  If you were running the previous package, your data is already at
  `data/dogar_trading.db` in that deployment — copy it across and skip the
  demo tenants (`tenant_acme`, `tenant_beta`), which are samples, not your
  records.

  Those two demo offices are **not shipped in the archive** (`data/` is
  excluded, so no customer records or databases travel with the release). They
  are *fixtures*: the branding and typeface verification harnesses log into
  `/t/acme` and assert on its accent colour and font. Create them when you want
  to run those harnesses:

  ```bash
  ./venv/bin/python scripts/provision_demo_tenants.py            # create
  ./venv/bin/python scripts/provision_demo_tenants.py --check    # verify only
  ```

  It never overwrites an existing tenant, so it is safe to re-run. Skip it
  entirely on a production install — nothing in the product needs it.

---

## 2. Run it — bare metal

```bash
# 2.1 System libraries (Debian/Ubuntu). See README "System Dependencies".
sudo apt-get update && sudo apt-get install -y \
    python3-venv libcairo2 libpango-1.0-0 libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 libffi-dev zbar-tools

# 2.2 Python environment
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2.3 Configuration — REQUIRED before first boot
cp .env.example .env
#   Set at minimum:
#     SECRET_KEY              generate: python -c "import secrets;print(secrets.token_urlsafe(48))"
#     DEFAULT_ADMIN_PASSWORD  the bootstrap admin's password
#   Optional: SMTP_* to enable self-service password reset.
#     Without it /api/auth/forgot-password returns 503 by design; admins can
#     still reset passwords from the Users screen.

# 2.4 Schema. Creates every table on a fresh install; idempotent, so it is also
#     safe to run against a database you copied in from a previous deployment.
alembic upgrade head

# 2.5 Serve
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open `http://<host>:8000/`. Health check: `GET /api/health`.

For a real deployment put it behind nginx/Caddy with TLS and run it under
systemd — a ready-made unit file is in `PROJECT_DOCUMENTATION.md` §12.

## 3. Run it — Docker

```bash
cp .env.example .env      # edit as above
docker compose up -d --build
```

## 4. Post-deploy verification

```bash
python -m pytest -q                          # expect 590 passed
python scripts/audit_template_geometry.py    # Overlay Engine geometry report
curl -s localhost:8000/api/health
```

## 5. Known open item — document template geometry

`scripts/audit_template_geometry.py` reports the current state of the coordinate
Overlay Engine. Nothing here crashes; one item needs a decision from you.

**One real defect.** On *Allied Bank Deposit Form*, the field
`customer_oep_license` sits at `y=1004.1` on a 792pt-tall page — 212pt above the
top edge — so ReportLab clips it, and the value has never appeared on any of the
15 documents generated from that template. It used to fail completely silently;
the engine now logs a warning and the audit reports it. It was **not** auto-moved,
because putting a licence number at an arbitrary spot on an official bank form is
worse than leaving it blank. **Open that template in the Template Designer and
drag the field to where it belongs** — that is the whole fix.

**Three aspect mismatches.** Templates 1, 7 and 8 draw an A4-proportioned scan
onto a Letter (612×792) page, stretching the artwork horizontally by +9.34%,
+9.34% and +11.29%. The overlaid values still land inside their boxes, because
the designer canvas, the HTML print view and the PDF engine all stretch the
artwork identically — which is exactly why this looks correct on screen and went
unnoticed. What is wrong is that the printed form's proportions no longer match
the official form.

This is also why the earlier repair appeared to regress: that fix cropped
`allied_bank_form7.jpg` to a true A4 aspect and added a guard, but the guard
measures each scan against a hardcoded A4 constant and only checks the page-size
*column default* — never the page size the template rows actually carry. So the
same distortion returned through the page-size axis, where nothing was watching,
and the suite stayed green. The new audit closes that hole by measuring every
scan against its own template's page.

Fixing the three templates means setting them to A4 (595×842); because their
coordinates were authored against the *stretched* artwork, every field must be
migrated by the same transform (template 7 has a field at `x+w=605`, which fits
Letter but not A4). That is a reviewable data migration on government forms, so
it is documented here rather than applied silently. Ask and it can be scripted
with a before/after render diff.

Full background: `PROJECT_DOCUMENTATION.md` §4.9 (geometry contract) and §4.13
(SPA design system).

---

## 6. Performance work in this build (measured, not asserted)

Everything below was measured on this machine against the real dataset (2,675
candidates / 2,267 demands / 1,246 clients), before and after.

### 6.1 Filter dropdowns were 28× heavier than the page itself

Opening `/candidates` fetched three reference lists to fill three pickers:

| Request | Before | After |
|---|---|---|
| `GET /api/clients/?limit=500` | 299,120 B · 0.121 s | **29,111 B · 0.034 s** |
| `GET /api/demands/?limit=500` | 489,635 B · 0.082 s | **36,524 B · 0.030 s** |
| `GET /api/lookups/visa-categories` | 37,222 B | 37,222 B (already small) |
| **Total on a cold mount** | **~826 KB** | **~103 KB** |

For comparison, the actual table data on that page is 29 KB. The app was moving
28× the payload of the page just to populate three `{value,label}` lists.

Two changes, both opt-in and both backwards compatible:

* **`compact=true`** on `/api/clients/` and `/api/demands/` — a two-column
  projection with no eager-loading and no per-row aggregation. Omitting the
  parameter returns the full serializer exactly as before, so no existing caller
  changed behaviour.
* **`app/frontend/src/lib/options.ts`** — a shared reference-list cache with a
  5-minute soft TTL, in-flight de-duplication and explicit invalidation when a
  record is created. Navigating away and back now refetches **nothing**.

### 6.2 Chart.js was blocking every page for a feature on one page

`app/frontend/index.html` loaded `chart.umd.min.js` as a classic, non-deferred
`<script>` in `<head>`: **205 KB / 68 KB gzipped**, parsed and executed *before*
the browser could even discover the React bundle — on every route, including the
login screen, for every user who never opens the Dashboard.

Chart.js is used by exactly one page, always through `ensureChart()`, so the tag
is gone and `src/lib/chart.ts` injects the same self-hosted bundle on first use.
Non-Dashboard routes now pay **zero** bytes for charting, and the Dashboard
fetches it in parallel with its own data instead of ahead of the bundle.

Verified rather than assumed: the served HTML contains no reference to the file,
the server logged its on-demand fetches returning 200, and the dashboard canvases
were sampled for **drawn pixels** — not merely checked for a `<canvas>` tag.

### 6.3 First-paint fonts are preloaded

Webfonts declared inside a stylesheet are only discovered after that stylesheet
is fetched *and parsed*, so the shell first-painted in fallback glyphs and then
reflowed. The three faces visible at first paint (Inter 400, Inter 600,
fa-solid-900) now preload alongside the HTML. Inter 500/700 and `fa-brands`
(108 KB) are deliberately **not** preloaded — they would only compete with the
bundle for bandwidth.

### 6.4 These are guarded, because a perf regression looks correct on screen

That is the whole problem with performance work: revert any of the above and
every functional test still passes, every page still renders, and nobody
notices for months. So each one has a guard:

| Guard | Pins |
|---|---|
| `tests/test_compact_projection.py` (10) | `compact=true` returns exactly the documented keys (no field leakage into a picker), agrees with the full serializer on total/ids/order/labels, and still honours `q` / `skip` / `limit` / auth |
| `tests/test_frontend_options_cache.py` (5) | Runs the Node cache check from `pytest`; static guard that no page bulk-fetches a reference list behind the cache's back |
| `app/frontend/tests/optionsCache.check.ts` (13) | Request counts, in-flight de-duplication, invalidation granularity, value semantics (ids for clients/demands vs **names** for lookup selects), failures not cached. Deterministic — no server, no browser, ~1 s |
| `tests/test_frontend_critical_path.py` (12) | Chart.js never returns as a blocking `<script>` in either the source or built HTML, the on-demand loader stays same-origin (the CSP blocks external scripts), and the three preloads keep `crossorigin` + `as="font"` |
| `tests/test_frontend_scroll_polish.py` (12) | Sticky data-grid headers stay attached to a scrollport that can actually scroll (see 6.5), the header stays opaque and flat, and the ≤640px stacked-card layout keeps document scrolling |
| `tests/test_fa_subset.py` (18) | The FontAwesome subset (6.6) is never stale, every icon referenced anywhere in the source has a rule in it, runtime-composed icon names are declared, FontAwesome's machinery survives, and all six shells link the subset rather than the full stylesheet |

Run the frontend cache check on its own with `npm run test:options` from
`app/frontend/`.

### 6.5 The sticky table header had never worked

Every list screen (Clients, Demands, Candidates, Users, …) renders its table as

```html
<div class="card overflow-hidden">
  <div class="overflow-x-auto table-scroll">
    <table class="data-table"> …
```

and `src/index.css` declared `.data-table thead th { position: sticky; top: 0 }`.
That declaration did nothing at all, and had done nothing since it was added.

The reason is a quiet corner of CSS Overflow: when one axis is set to something
other than `visible`, the **used value of the other axis becomes `auto` too**.
`.table-scroll` only asks for `overflow-x: auto`, but it was therefore already a
scroll container on *both* axes. It had no height limit, so its `scrollTop` was
permanently `0` — the document scrolled instead. A sticky element only ever
moves relative to its nearest scrollport, and here that scrollport never moved.
The header was sticky in the stylesheet and static on screen.

The fix gives the box a genuine bounded height, desktop only:

```css
@media (min-width: 1024px) {
  .table-scroll {
    max-height: max(420px, calc(100dvh - 300px));
    overflow-y: auto;
    overscroll-behavior: contain;   /* don't hand the wheel back mid-list */
    scrollbar-gutter: stable;       /* no ~9px column jump past the fold */
  }
}
```

`max-height` (not `height`) means tables shorter than the viewport are
completely unaffected — the box only starts scrolling at the point where a
stuck header earns its keep. Below 1024px the list switches to the stacked-card
layout, where document scrolling is the correct model, so the rule is not
applied there.

Two supporting changes were needed to make a stuck header look right:

* the header background became a **flat** `#f8fafc` instead of a two-stop
  gradient — a sticky header has to be fully opaque or scrolled rows show
  through it, and the flat fill also matches the house style and avoids
  repainting a gradient on every scroll frame;
* the bottom rule is drawn with `box-shadow: inset 0 -1px 0` rather than
  `border-bottom`, because a real border on a sticky `<th>` is dropped by the
  `border-collapse` painting rules.

`tests/test_frontend_scroll_polish.py` pins the invariant *as a pair*: if
`thead th` is sticky, `.table-scroll` must also be bounded. The guards were
mutation-tested — reverting the media query to the previous state fails four of
them, so they are not vacuous.

Verified in a real browser at 1440×900, scrolling each grid to its end and
comparing the header's viewport position against the scroll box's:

| Route | scrolled | box top | header top | stuck |
|---|---|---|---|---|
| `/clients` | 344 px | 306 | 306 | yes |
| `/candidates` | 747 px | 278 | 278 | yes |
| `/demands` | 514 px | 306 | 306 | yes |
| `/users` | 0 px | 208 | 208 | n/a — see below |

`/users` is the case that proves `max-height` was the right choice: its table is
373 px tall, `clientHeight == scrollHeight == 373` against a 600 px cap, so the
box was *not* padded out to 600 px and does not scroll at all. Short lists are
untouched. The header's computed `background-image` reads `none` on every route,
confirming the gradient is gone.

### 6.6 69% of the FontAwesome stylesheet was for icons this portal never uses

`all.min.css` is the complete FontAwesome 6.4.0 Free stylesheet — 101,965 bytes,
22,373 gzipped — and it render-blocks **both** shells, so every user paid for it
on every route. 70,799 of those bytes (69%) are the per-icon `content:` rules
for all 1,856 icons in the set. A scan of the entire source tree found the
portal references **183**.

`scripts/build_fa_subset.py` now generates `fa-subset.min.css`: FontAwesome's
machinery is copied verbatim (`@font-face`, the family bindings, sizing,
rotation, animation, stack and list utilities, keyframes, custom properties) and
only unused per-icon rules are dropped. Where an icon rule carries aliases, the
unused aliases are pruned from the selector list rather than the rule being
dropped.

| | bytes | gzipped |
|---|---|---|
| `all.min.css` | 101,965 | 22,373 |
| `fa-subset.min.css` | 23,779 | 4,448 |
| **saved on every route** | **78,186 (76.7%)** | **17,925 (80.1%)** |

Effect on the whole render-blocking CSS budget, measured over HTTP against the
running app with `Accept-Encoding: gzip`:

```
before: 48,030 B gzipped   (all.min.css 22,373 + fontawesome.css 194 +
                            inter.css 206 + app.css 11,255 + spa css 14,002)
after : 30,087 B gzipped   -- 37.4% less blocking CSS on every route
```

There are also ~1,700 fewer selectors for the style engine to match on every
recalculation, which is the part that matters most on low-end mobile.

**If you add an icon, regenerate**, or it renders as a blank box:

```bash
python scripts/build_fa_subset.py        # rewrite the subset
python scripts/build_fa_subset.py --check   # CI-style verification
```

`tests/test_fa_subset.py` fails if you forget. The riskiest case is an icon name
built at runtime — `fa-align-${a}`, `fa-chevron-${open ? 'up' : 'down'}` — which
no static scan can see. Three such sites existed and were missed on the first
pass; they are now declared in the generator's `DYNAMIC` map, and a test finds
every interpolation site and fails if a new one is not declared. Both of these
guards were mutation-tested: adding an un-regenerated icon fails two tests, and
deleting a `DYNAMIC` entry fails the interpolation test with the exact file and
line.

Verified in a real browser rather than only on paper. On each of `/dashboard`,
`/clients`, `/candidates`, `/demands` and `/users`, every `fa-*` element on the
page was read back through `getComputedStyle(el, '::before').content`, which is
empty exactly when an icon's rule is missing:

| Route | distinct icons rendering a glyph | missing |
|---|---|---|
| `/dashboard` | 27 | 0 |
| `/clients` | 24 | 0 |
| `/candidates` | 25 | 0 |
| `/demands` | 24 | 0 |
| `/users` | 28 | 0 |

`all.min.css` is deliberately still on disk. Rolling back is changing the six
`<link>` hrefs back.

### 6.7 What was NOT measured here, and why

Browser frame-rate and scroll-jank numbers are **not** reported. This build
machine has 985 MB of RAM and 2 cores; Chromium is swapped out under load
(`kswapd0` active) and was OOM-killed twice during this session. Any FPS figure
produced here would be an artefact of the sandbox, not of the application, so
none is quoted. Payload sizes, request counts, server latencies and DOM facts
are unaffected by memory pressure — those are what the numbers above report.

The sticky-header fix (6.5) and the icon subset (6.6) *were* confirmed in a real
browser, but only on the third attempt — the first two failed on this machine
(Chromium would not navigate under `--single-process`, then was starved out at
95 MB available against roughly 300 MB needed). They are reported in 6.5 and 6.6
as measurements because they are measurements; everything else in section 6 is
payload bytes and request counts, which memory pressure cannot distort.

---

## 7. Security work in this build — a CSP with zero exceptions on documents

Until this build the Content-Security-Policy carried exactly one deliberate
hole: `style-src-attr 'unsafe-inline'`. It was accepted on 2026-07-28 after
being driven in a real browser and shown to be inert (a style *attribute*
cannot execute script; injected `<style>` elements and inline `<script>` were
both already blocked). That acceptance still stands where it must. It no longer
applies to the document routes.

### 7.1 Why the exception could not simply be removed

CSP has **no nonce mechanism for style attributes**. A nonce attaches to an
*element*; `style="…"` is not one. So `'unsafe-inline'` cannot be swapped for a
nonce the way it was for `<script>` and `<style>` — it can only be **deleted**,
which means the attributes themselves have to stop being emitted.

For the standalone documents that is achievable, because they are rendered
server-side in a single pass with no runtime DOM building: every style attribute
is known at render time. `app/core/style_hoist.py` rewrites the finished HTML,
moving each attribute into a nonced `<style>` element behind a hook:

```html
<!-- before -->
<div class="field" style="left:12mm;top:40mm">

<!-- after -->
<div class="field" data-csp-h="h3">
<style nonce="…">[data-csp-h="h3"][data-csp-h]{left:12mm;top:40mm}</style>
```

An un-nonceable construct becomes a nonceable one. These four routes now serve
with `style-src-attr 'none'`:

| Route | Renderer |
|---|---|
| letterhead / undertaking documents (12) | `app/services/letterhead_renderer.py` |
| receipts | `app/services/receipts_renderer.py` |
| designer print preview | `app/api/endpoints/documents.py` |
| candidate profile print | `app/api/endpoints/candidates.py` |

The tightening is **per response**. The Jinja and SPA shells keep the accepted
exception, and still need it: `app/static/js/safe_dom.js` calls
`setAttribute('style', value)` on nodes it builds at runtime, which no
server-side rewrite can reach.

### 7.2 Why the documents still look identical

A style attribute outranks every selector; a rule does not. Hoisting therefore
risks losing to a class rule that the attribute previously beat. The generated
selector is **doubled** — `[data-csp-h="h3"][data-csp-h]` — for specificity
**(0,2,0)**: above the documents' class rules, and deliberately *below*
`!important`, because the print stylesheets use `!important` overrides that must
keep winning exactly as before. Checked against the real letterhead and designer
CSS: neither uses ID selectors, so nothing can reach (1,0,0) and outrank the hook.

That reasoning was then verified rather than trusted:

| Check | Result |
|---|---|
| Computed styles, original vs hoisted (Chromium) | `e_barcode` 16 elements — **0** property diffs, **0** geometry diffs; `undertaking_b` 20 elements — **0** / **0** |
| Live routes under the enforced header | `candidate_profile`, `protector_main`, `undertaking_b`, `designer_print` — **0** CSP violations each, **0** server-rendered style attributes left, hoisted rules present in the CSSOM (7 / 7 / 14), absolutely-positioned overlay fields still `position: absolute` (10 on `designer_print`) |

### 7.3 What was NOT closed here, and why that is correct

`designer_print` still shows one live `style` attribute in the DOM:
`style="--fit-scale: 1;"`, written by `body.style.setProperty()`. It produced
**zero** CSP violations. CSSOM writes are **exempt** from `style-src-attr` — the
directive governs *parsing* a style attribute, not the property-level API. This
was measured in Chromium, not assumed, and it is both the reason the hoister only
needs to handle server-rendered markup and the route by which the shells could
one day drop their exception too (migrate `safe_dom.js` off
`setAttribute('style', …)` onto per-property writes).

### 7.4 Two defects found while wiring this up

Both are now pinned by tests in `tests/test_style_hoist.py`.

1. **A regex-segmented scan was unsafe.** A literal
   `</style><script>alert(1)</script>` sitting inside *another attribute's value*
   matched the protected-region pattern and forged a segment boundary, so the
   real style attribute in that tag was never sanitised and `alert(1)` survived.
   Replaced with a quote-aware scanner that only ever sees genuine tag and
   raw-text-element boundaries.
2. **Splitting declarations on `;` corrupted every document background.**
   `background-image:url('data:image/png;base64,…')` was cut in half. No test
   covered this; it was found by reading the code, and it would have rendered
   every background blank. The splitter is now quote- and paren-aware and splits
   on top-level `;` only.

Declarations containing `<`, `{`, `}` or `@` are dropped rather than escaped: a
`<style>` body is a raw-text element and ends at the first `</style`, *even
inside a CSS string literal*, so no encoding makes those characters safe there.

### 7.5 Verification

```bash
./venv/bin/python -m pytest tests/test_style_hoist.py -q          # 21 passed
./venv/bin/python -m pytest tests/test_csp_zero_exception.py -q   #  5 passed
./venv/bin/python -m pytest -q                                    # 590 passed
```

Both new modules are **mutation-verified**. The middleware signal crosses a
`BaseHTTPMiddleware` boundary, where Starlette runs the downstream app in a
*child* `Context` — a plain `ContextVar.set()` from a route is invisible to the
wrapping middleware, while mutating a dict the middleware already holds does
cross. Replacing the mutable container with the naive `set()` version makes both
integration tests fail, as it must. `tests/test_bg_fit.py`, taught to read the
hoisted rule instead of the attribute, was likewise re-confirmed to still catch
the original background-stretch bug.

Full design record: `PROJECT_DOCUMENTATION.md` §7.3.1.
