# Enhancements — 2026-07-10 (Session 2)

> **📁 ARCHIVED — point-in-time record.** This report describes the repository as it stood on **2026-07-10**.
> Paths, filenames, counts and version numbers in it are deliberately **not**
> refreshed as the code moves on, so the document stays an honest account of
> what was actually found and fixed then. Some assets it names have since been
> renamed or relocated. **For the current state of the system, read
> `PROJECT_DOCUMENTATION.md` and `README.md` at the project root — not this file.**


Follow-up to `FIXES_APPLIED_2026-07-10.md`. Seven user-requested improvements,
each implemented, verified live, and covered by automated tests where noted.

All work landed on `main`. Full regression: **144 pytest passed**, **ruff clean**,
**E2E smoke 5/5**, **E2E reconnect 7/7**.

---

## 1 & 2 & 6 — Document Coordinate Engine: background-image fit modes (fixes auto-percentage + barcode fidelity)

**Problem reported:** background image was stuck at an "auto" zoom (47 %, 44 %) and
was *stretched* to the page, distorting scans and, critically, **barcodes** so they
could stop scanning.

**Root cause:** the designer canvas used `objectFit: 'fill'` and the PDF engine
rendered with `preserveAspectRatio=False` — both **stretch** the image to the page box.

**Fix — three explicit fit modes** (selectable in the designer toolbar, persisted in
the template's `designer:` metadata fence as `bg_fit`, no DB migration needed):

| Mode | Behaviour | Best for |
|------|-----------|----------|
| **Fill page (stretch)** | stretches to the page box (legacy default) | full-page scans |
| **Contain (keep ratio)** | aspect-preserved, fit inside page, top-left anchored | barcodes, logos |
| **Actual size (1:1)** | intrinsic pixels → points, top-left anchored | most faithful reproduction |

- Frontend (`DocumentCustomize.tsx`): fit-aware canvas preview, captures the image's
  natural size, `imageRendering: crisp-edges` for non-fill modes.
- PDF engine (`pdf_engine.py`): new `_bg_placement(fit, iw, ih, w, h)` geometry helper;
  `_draw_background(..., fit=)` uses **lossless PNG** (not JPEG) for `contain`/`actual`
  so **barcodes never blur and stay scannable**, and preserves alpha.
- Tests: `tests/test_bg_fit.py` — 6 tests, all pass (fill full page, contain preserves
  aspect / uses limiting dimension, actual keeps intrinsic size + top-anchored,
  zero-size and unknown-fit both fall back to fill).

**Barcode answer:** upload the barcode as the background and pick **Contain** or
**Actual size** — the engine keeps the exact aspect ratio and renders it losslessly,
so the printed PDF is a faithful, scannable copy (no distortion).

---

## 3 — Login & create-account UI polish

- `Login.tsx`: per-field client validation (email regex + required password),
  focuses the first offending field, red border/icon states, `aria-invalid` /
  `aria-describedby`, `onBlur` validation, `type="email"` / `inputMode="email"`,
  a shake-animated error banner (`role="alert" aria-live="assertive"`) with a
  clear "Sign in failed" heading and improved spacing.
- `Users.tsx` (create account): trimmed name/email, email-format check, password
  ≥ 8 chars for new users, friendlier toasts (e.g. `Account for "…" created.`).

---

## 4 — Image upload & preview flow polish

- New reusable **`ImageUpload`** component (`components/ImageUpload.tsx`):
  drag-and-drop + click, crisp local preview via `URL.createObjectURL`
  (revoked on unmount), consistent aspect-ratio crop box, type/size validation
  with inline errors, hover "Change" overlay, **progress bar** during upload,
  and a Remove button. Parent owns the actual upload; the component owns local
  selection/preview.
- Wired into the **tenant logo** (`MiscPages.tsx`, `fit=contain`, 1:1, real axios
  `onUploadProgress`) and the **candidate photo** (`AssignWizard.tsx`, `fit=cover`,
  3:4 passport crop).

---

## 5 — No more full-page reloads on navigation

**Problem reported:** navigating felt like the *entire* page reloaded instead of the
sidebar staying mounted and only the content swapping.

**Root cause:** a single `<Suspense>` in `App.tsx` wrapped the **whole** route tree
(including `AppLayout` / sidebar), so a lazy chunk load unmounted everything and
showed a bare full-screen fallback.

**Fix:** moved the `<Suspense>` boundary **inside `AppLayout`**, wrapping only the
`<Outlet>`. Now a lazy route load blanks **only the content pane** — the sidebar and
topbar stay mounted. Verified: no remaining raw internal `<a href>` links (only the
intentional server-rendered `/privacy` page and the `/t/<slug>/` tenant switch, which
correctly full-reload). Covered by the smoke test's "SPA nav (no full reload)" check.

---

## 7 — Reconnect (offline → online) refetch, no stale rows

- New **`useReconnect`** hook (`lib/useReconnect.ts`): fires a callback on the browser
  `online` event and on `visibilitychange` returning to visible with a live connection
  (covers wake-from-sleep / tab refocus). Returns `online` for UI.
- `Candidates.tsx`: shows an **offline banner** (`data-testid="offline-banner"`) while
  offline; on reconnect it refetches the list **and** bumps a `refreshKey` so the open
  candidate side sheet refetches its detail — the user never sees rows/details cached
  while the connection was down.
- E2E: **`scripts/e2e_reconnect.py`** — logs in, opens Candidates, opens a candidate
  side sheet, forces `set_offline(True)` (asserts banner shows + no refetch), then
  `set_offline(False)` (asserts list refetch, banner clears, side-sheet detail refetch).
  **7/7 checks pass** against the live URL.

---

## Verification snapshot

```
pytest:            144 passed
ruff:              All checks passed!
e2e_smoke.py:      5/5 PASS  (Bug#1 login, Bug#2 SPA nav, Bug#3 drawer)
e2e_reconnect.py:  7/7 PASS  (offline banner + list & side-sheet refetch on reconnect)
tsc -b:            exit 0
SPA build:         success (served from app/static/spa/)
```

Run the E2E tests yourself:

```bash
venv/bin/python scripts/e2e_smoke.py     <BASE_URL> admin@yourcompany.com <PASSWORD> 2264
venv/bin/python scripts/e2e_reconnect.py <BASE_URL> admin@yourcompany.com <PASSWORD>
```
