# Enhancements — 2026-07-11

> **📁 ARCHIVED — point-in-time record.** This report describes the repository as it stood on **2026-07-11**.
> Paths, filenames, counts and version numbers in it are deliberately **not**
> refreshed as the code moves on, so the document stays an honest account of
> what was actually found and fixed then. Some assets it names have since been
> renamed or relocated. **For the current state of the system, read
> `PROJECT_DOCUMENTATION.md` and `README.md` at the project root — not this file.**


This round addresses the 8 concerns raised after the video review of
**National Bank of Pakistan Fee.mp4** and **OEP Form.mp4**, plus the Roles,
barcode, and performance requests.

## 1. NBP Fee — one complete 2-page document
- The deposit slip now renders as a single **2-page** document
  (`NBP Deposit Slip`): page 1 = Bank Copy + Depositor Copy, page 2 =
  Director General Copy + OPF Copy.
- Implemented via **per-page backgrounds**: designer meta key
  `page_backgrounds` maps a page number to its own scanned background image
  (`{"2": "nbp_deposit_slip.jpg"}`). Page 1 uses `template.background_image`.
- Page-1 (6) and page-2 (6) field layouts seeded.

## 2. OEP Form — front + back in ONE document
- The OEP registration form is now a single **2-page** document
  (`OEP Form`): page 1 = front (registration / personal / passport, 14
  fields, background `oep_form_p1.jpg`); page 2 = back (Next of Kin, fee
  table, job & employer details, 8 fields, background `oep_form_p2.jpg`).
- The old standalone **"OEP Form Page 2"** template has been retired.

## 3. Per-page background engine
- Both the **print HTML** engine (`app/api/endpoints/documents.py`) and the
  **PDF** engine (`app/services/pdf_engine.py`) now resolve a distinct
  background per page from the `page_backgrounds` designer meta, keeping the
  existing path-traversal guards. Verified: OEP Form → 2 pages, NBP Deposit
  Slip → 2 pages.

## 4. Correct document name: "HBL" → "NBP Form 32"
- The template previously mis-labelled *"HBL - Overseas Employment
  Corporation"* is renamed to **"NBP Form 32"** in the seed data, and an
  idempotent rename-in-place migration preserves its field coordinates.

## 5. Professional document list
- Removed the PDF icon and the blue guided-text banner from the candidate
  **Documents** tab. Sections are now clean `<section>` blocks with uppercase
  section headers, tidy spacing, and a single clear **Print** action per row.

## 6. Roles — REAL access control (not a read-only display)
- Rebuilt the Roles page (`app/frontend/src/pages/MiscPages.tsx`) into a full
  RBAC editor:
  - **Add New Role** button.
  - Permission **checkbox matrix** driven by
    `GET /api/users/roles/_meta/catalog` (all 11 modules).
  - Per-module **Select all** with indeterminate state.
  - **Preset** buttons (admin / manager / staff defaults) + **Clear all**.
  - **Customize** (edit) and **Delete** (non-system roles only).
  - Create → `POST /users/roles`; edit → `PUT /users/roles/{id}`;
    delete → `DELETE /users/roles/{id}`.
  - System-role names are locked; the Admin role is forced to keep the
    wildcard `*` (full access).

## 7. Barcode — professional look & consistent size
- Each barcode is now a **labelled card** in `render_e_barcode()`:
  - Field label on top (**E-Number** / **Passport No**).
  - Barcode centred in a clean **bordered quiet-zone box** (ISO/IEC 15417).
  - Raw value shown as a monospace caption underneath.
- Both cards share an **identical fixed width (78 mm) and box height (20 mm)**
  so the two barcodes look uniform regardless of value length. Bars render as
  crisp resolution-independent SVG.

## 8. Performance
- **Static caching**: content-hashed SPA bundles under `/static/spa/assets/`
  now get a **1-year immutable** `Cache-Control` (repeat navigations are
  effectively instant); other `/static/` assets keep the 1-day cache.
- **Printing**: `printInFrame()` now fires the print dialog as soon as the
  document's images (barcodes / scanned backgrounds) finish decoding, instead
  of a blind 400 ms wait — faster on simple docs, still reliable on
  image-heavy ones, with a 1.2 s safety cap.
- Route-level code-splitting (lazy pages) and 30 s read-only API caching were
  already in place and retained.

## 9. Real-time notifications in the header bell
- The header bell was a dead button — it is now a live **NotificationBell**
  dropdown (`app/frontend/src/layout/NotificationBell.tsx`) wired to the
  existing reminders API:
  - Polls `GET /api/reminders/summary` every 30 s for the **unread badge**
    (shows `99+` when large).
  - Loads `GET /api/reminders?state=active` on open, severity-sorted, with
    per-item icons (critical / warning / info).
  - Clicking a reminder marks it read (`POST /reminders/{id}/read`) and opens
    the linked candidate / demand record.
  - **Dismiss** (`/reminders/{id}/dismiss`), **Mark all read**
    (`/reminders/read-all`) and **Scan now** (`/reminders/scan`) actions.
- Verified live: summary 200, list 200 with items.

## 10. Change Password in the profile dropdown
- Added a **Change Password** item to the header profile menu plus a working
  modal (`ChangePasswordModal` in `Topbar.tsx`):
  - Current / new / confirm fields, show-passwords toggle, client-side
    validation (min length, match, must differ), then
    `POST /api/users/me/change-password`.
  - Success/error surfaced via the app toast.
- Verified live: endpoint correctly rejects a wrong current password (400
  "Current password is incorrect").

## 11. DtcBot fixes (print + add-client)
- **In-chat printing fixed**: the flow engine returns `print` as an object
  `{tpl_id, record_id, title}` and print chips as `print:<tpl>:<rec>`, but the
  React widget was passing the raw object / mis-sliced string to
  `printInFrame`, so nothing printed. Added `resolvePrint()` +
  `printUrl()` helpers that normalize any form to
  `/api/documents/templates/<tpl>/print?record_id=<id>`.
- **Add-new-client**: the flow is fully functional (creates a real `Client`,
  returns a client card + follow-up chips). The offline **fallback** welcome
  was missing the "Add new client" chip — added it so it matches the trained
  `/chatbot/welcome` (Add demand · **Add client** · Find demand · Find
  candidate · Print).

## Validation
- `pytest`: **144 passed**.
- `ruff check app scripts`: **All checks passed!**
- `tsc --noEmit`: clean (both rounds).
- SPA rebuilt (`vite build`) — `MiscPages` ships the Roles UI; `index` bundle
  ships the NotificationBell + ChangePassword modal.
- Live smoke: login 200; roles catalog 11 modules; roles list 3; E-Barcode
  200 with labelled cards; OEP Form / NBP Deposit Slip → 2-page PDFs;
  reminders summary + list 200; change-password rejects wrong current pw (400).

---

## Round C — mobile UX + streamlined DtcBot client flow (follow-up)

### 12. DtcBot "Add new client" — streamlined flow (per spec)
The previous add_client flow asked 9 questions (contact person, license,
sponsor name, sponsor phone, sponsor Iqama, etc.) — those belong to a *demand*,
not a *client*, and made the bot feel wrong. Rebuilt the flow to the exact
requested sequence:

  **name → Individual/Company → phone → country → Active/Inactive → confirm**

- Country step now offers Pakistan · Saudi Arabia · UAE · Qatar · Kuwait ·
  Oman · Bahrain (+ Skip → defaults to Pakistan).
- Status step (Active/Inactive) is new and maps to `Client.status`.
- Confirmation card shows only Name / Type / Phone / Country / Status.
- Verified live end-to-end: creates a real `Client` row (e.g. ID 1246) and
  returns a client card + follow-up chips.

### 13. Change Password modal — mobile rendering fixed
On phones the modal was vertically centred, so the on-screen keyboard pushed
the top fields off-screen (screenshot showed only "Confirm New Password"
visible). Now: `items-start` on mobile (centred on ≥sm), `my-8`, `max-h-[90vh]`
with an internal scroll region, and the overlay itself scrolls. All three
fields + the toggle + buttons are always reachable.

### 14. My Profile vs Settings — distinct actions
Both "My Profile" and "Settings" previously navigated to `/settings`
(redundant, and `/settings` is Admin/Manager-gated so ordinary users hit
Unauthorized). Now:
- **My Profile** → opens a self-service **Account modal** (avatar, name, email,
  role) available to every user, with a "Change Password" shortcut.
- **Settings** → `/settings` (unchanged, still permission-gated).

### 15. Mobile: notification bell + profile dropdown clickable
The bell's wrapper used `onClick={stopPropagation}` which, combined with the
toggle, could swallow taps on mobile. Rewired NotificationBell with a proper
`ref`-based outside-click close, a 40×40 touch target, and
`touch-manipulation`. The bell now opens/closes reliably on touch and closes
when tapping elsewhere.

## Validation (Round C)
- `pytest`: **144 passed**.
- `ruff check app scripts`: **All checks passed!**
- `tsc --noEmit`: clean.
- `vite build`: success (`index-C5KFXESb.js`).
- Live smoke: DtcBot add_client full flow → real Client created (ID 1246) with
  Name/Type/Phone/Country/Status only.
