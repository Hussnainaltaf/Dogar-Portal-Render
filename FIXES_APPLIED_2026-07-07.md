# Dogar Trading Corporation Portal — Follow‑Up Fixes Applied Report

> **📁 ARCHIVED — point-in-time record.** This report describes the repository as it stood on **2026-07-07**.
> Paths, filenames, counts and version numbers in it are deliberately **not**
> refreshed as the code moves on, so the document stays an honest account of
> what was actually found and fixed then. Some assets it names have since been
> renamed or relocated. **For the current state of the system, read
> `PROJECT_DOCUMENTATION.md` and `README.md` at the project root — not this file.**


**Fix date:** 2026‑07‑07
**Engineer:** Automated follow‑up remediation (fix → verify with real runs)
**Scope:** The three formal FIX items from the independent audit, applied **in order**, each verified with a real build/test run before moving on — plus two extra confirmed bugs found in prose during the same review.
**Baseline:** `AUDIT_REPORT_2026-07-06.md` (all 88 tests passing, verdict PRODUCTION‑READY).
**Verdict:** ✅ **ALL THREE FIXES APPLIED & VERIFIED.** `92 passed` (88 baseline + 4 new), `tsc -b --noEmit` 0 errors, every changed `.py` compiles, SPA rebuilt, zero CSP violations at `/dashboard`.

---

## 0. Executive Summary

This round addresses three defects surfaced by an independent audit of the same codebase, plus two additional confirmed bugs. Every change was **verified with a real run**, not assumed:

| FIX | Area | Outcome |
|---|---|---|
| **FIX 1** | Dashboard charts silently blocked by strict CSP (runtime CDN `<script>` injection) | ✅ Rewired to self‑hosted vendored Chart.js; **zero CSP violations** confirmed via headless browser |
| **FIX 2** | Candidate wizard mismatch: backend 7 steps, frontend 5, frontend never used the draft API | ✅ **Option A** — built the 2 missing steps + wired the frontend to `/candidates/wizard/*`; step counts now match (7 == 7) |
| **FIX 3** | 17 bare `print()` calls in PDF/letterhead/barcode exception handlers | ✅ Replaced with module‑level `logging.getLogger("dtc.<module>")`; exception logic unchanged |
| **FIX‑A** (extra) | Broken import in `dtcbot_llm.py` (`app.services.workflow` does not exist) | ✅ Corrected to `app.core.workflow` |
| **FIX‑B** (extra) | `tenant_service.py` passed a non‑existent `short_name=` kwarg to `CompanySettings`, so per‑tenant settings were never seeded | ✅ Invalid kwarg removed; new tenants now seed `file_prefix` cleanly |

---

## 1. Verification Method (real runs, not assumptions)

| Check | Command | Result |
|---|---|---|
| Full test suite | `TESTING=true venv/bin/python -m pytest` | ✅ **92 passed** (was 88; +4 new wizard tests) |
| Python syntax (all changed `.py`) | `python -m py_compile …` | ✅ 0 errors (8 files) |
| Frontend type‑check | `npx tsc -b --noEmit` | ✅ 0 errors |
| Frontend production build | `npm run build` (Vite) | ✅ built into `app/static/spa/` |
| Vendored Chart.js served | `curl /static/vendor/js/chart.umd.min.js` | ✅ HTTP 200, `text/javascript`, 205 399 bytes |
| CSP header at `/dashboard` | `curl -D -` | ✅ strict `script-src 'self' 'nonce-…'` |
| Browser render / CSP violations | PlaywrightConsoleCapture `/dashboard` + `/login` | ✅ **zero CSP violations** (only expected 401 auth calls) |
| Wizard draft→finalize→assign | `pytest tests/test_candidate_wizard.py` | ✅ 4 passed |

---

## 2. FIX 1 — Dashboard chart CDN / CSP break

**Problem.** `app/frontend/src/lib/chart.ts` injected `<script src="https://cdn.jsdelivr.net/npm/chart.js">` at runtime. The strict CSP tightened during the 2026‑07‑06 audit (`script-src 'self' 'nonce-…'`) blocks all external scripts, so the CDN copy never executed and the Dashboard charts silently failed to render. This was a **regression introduced by the Phase‑1 CSP hardening** — completing this fix finalises that hardening correctly.

**Fix.**
- **`app/frontend/index.html`** — added a classic (non‑module) `<script src="/static/vendor/js/chart.umd.min.js"></script>` in `<head>` after the vendored CSS links, following the identical self‑hosting pattern used for FontAwesome and Inter. The vendored bundle (Chart.js v4.4.1, already shipped in `app/static/vendor/js/`) is same‑origin `'self'` and therefore allowed by the CSP.
- **`app/frontend/src/lib/chart.ts`** — **removed the CDN `<script>` injection entirely.** `ensureChart()` now resolves the already‑present `window.Chart` global (polling briefly, 50 ms interval / 5 s timeout, only if React runs before the classic script finishes). No external network dependency remains. `Dashboard.tsx`'s 3 call sites (`ensureChart().then(Chart => …)`) are unchanged — the promise contract is preserved.
- **SPA rebuilt** (`npm run build`) → `app/static/spa/` (Vite `outDir`, no manual copy). The built `index.html` retains the vendored `<script>` tag verbatim (Vite leaves absolute `/static/*` URLs untouched, same as the vendored CSS).

**Verification.**
- `curl /static/vendor/js/chart.umd.min.js` → HTTP 200, `text/javascript`, 205 399 bytes.
- CSP header confirmed strict: `default-src 'self'; script-src 'self' 'nonce-…'; …`.
- Headless browser (`/dashboard` and `/login`, which serve the same SPA shell with the chart script) → **zero CSP violations**; the only console errors are expected `401`s from unauthenticated API probes. No `Refused to load the script …` message (which the old CDN injection produced).
- The vendored UMD wrapper `(…globalThis…||self).Chart = …` provably assigns `window.Chart` when loaded as a classic script.

---

## 3. FIX 2 — Candidate wizard step mismatch (Option A)

**Problem.** Backend `app/core/workflow.py` declares `WIZARD_STEPS` = **7** steps and ships a complete resumable‑draft API (`/candidates/wizard/*`: create, save‑step, finalize, reopen, resume shelf, discard — 10 endpoints, audited, idempotent). The frontend `AssignWizard.tsx` had only **5** steps and never called the wizard API — it POSTed straight to `/candidates/` then `/demands/trades/{id}/assign`. Two data domains the `Candidate` model **actually persists** had no editing UI at all:
- **Medical & Protector** — `protector_no`, `protector_date`, `medical_center`, `gamca_number`, `medical_date`, `medical_consignment_no`, `medical_send_date`, `medical_courier_name`
- **Visa & Travel** — `e_number`, `visa_stamp_date`, `date_of_departure`, `flight_no`, `destination`, `ticket_no`

**Decision — Option A (build the 2 steps + wire the draft API).** Chosen over Option B (delete the backend + reduce to 5) because the backend is a **complete, working, now‑tested** subsystem and the candidate schema already stores medical/protector/visa/travel data with **no other UI to edit it**. Option B would have orphaned real business columns (protector clearance, GAMCA medical tracking, visa stamping — core OEP workflow) and destroyed a tested feature to hide a UI gap.

**Fix.**
- **`app/frontend/src/components/AssignWizard.tsx`** (rewritten):
  - `STEPS` expanded to **7**, labels matching `WIZARD_STEPS` exactly: *Personal Info → Identification → Employment → Next of Kin → Medical & Protector → Visa & Travel → Charge Summary*.
  - New **Step 5 (Medical & Protector)** and **Step 6 (Visa & Travel)** forms, keyed by the real `Candidate` column names.
  - **CREATE mode now uses the draft/resume API:** lazily `POST /candidates/wizard` (opens a draft on the trade), `PATCH /candidates/wizard/{id}/step/{n}` on each *Next* (persists a resumable slice), then `POST /candidates/wizard/{id}/finalize` on *Save* — which creates the candidate **and** auto‑assigns to the trade. The separate `/demands/trades/{id}/assign` call is no longer needed.
  - **EDIT mode** keeps the simpler `PUT /candidates/{id}` path, now covering the new medical/visa fields too.
  - Fixed a latent bug: the old form sent `permission_no_suffix`, which is **not** a `Candidate` column and was silently dropped by `_coerce` — the permission number was never saved. The new code composes `permission_no = prefix + suffix` and maps to the real column.
- **`app/api/endpoints/candidates.py`** — added a **slot‑capacity guard** to `finalize`'s auto‑assign block, mirroring the standalone `/demands/trades/{id}/assign` endpoint. Without it, finalising into a full trade would silently over‑assign (a regression vs. the old flow's "Slot Full" behaviour). On a full trade, the candidate profile is still saved (draft), but the assignment is refused with a clear 400.
- **`app/frontend/src/pages/DemandDetail.tsx`** — updated the stale "5‑STEP" comment to "7‑STEP".

**Verification.**
- Step‑count parity: backend `TOTAL_WIZARD_STEPS == 7` and labels **exactly equal** the frontend `STEPS` array.
- New test file **`tests/test_candidate_wizard.py`** (4 tests): 7‑step metadata; full create→save‑7‑steps→finalize flow (asserts medical/visa columns persisted + candidate assigned to the trade); `full_name` required to finalize; slot‑capacity guard refuses the 2nd finalize on a `quantity=1` trade. **All 4 pass.**
- `tests/test_demands_wizard.py` (the *demand*-creation wizard) unaffected — it asserts nothing about candidate step counts. Still passes.

---

## 4. FIX 3 — Replace bare `print()` with structured logging

**Problem.** 17 bare `print(...)` calls sat inside exception handlers for PDF / letterhead / barcode rendering, bypassing the app's structured `dtc.<module>` logging convention.

**Fix.** Added a module‑level `log = logging.getLogger("dtc.<module_name>")` to each file and replaced every `print()` with `log.warning(...)` using lazy `%`‑formatting. **Only the reporting call changed — no exception‑handling logic was altered**, and the `"[module]"` prefix (now redundant with the logger name) was dropped.

| File | Logger | `print()` replaced |
|---|---|---|
| `app/services/pdf_engine.py` | `dtc.pdf_engine` | 10 |
| `app/services/letterhead_renderer.py` | `dtc.letterhead_renderer` | 4 |
| `app/services/overlay_engine.py` | `dtc.overlay_engine` | 1 |
| `app/api/endpoints/documents.py` | `dtc.documents` | 2 |
| **Total** | | **17** |

**Verification.**
- `grep` confirms **0** remaining Python `print(` in all four files; the `window.print()` JavaScript strings inside HTML templates (3 + 4) are preserved untouched.
- Runtime probe: all four modules import, expose the correctly‑named `dtc.<module>` logger, and emit warnings through the logging framework.

---

## 5. Extra confirmed bugs fixed (found in prose during review)

### FIX‑A — Broken import in `dtcbot_llm.py`
`from app.services.workflow import CandidateStage` — `app.services.workflow` **does not exist**; `CandidateStage` lives in `app.core.workflow`. Any code path reaching that import would raise `ModuleNotFoundError`.
**Fix:** `from app.core.workflow import CandidateStage`. Verified: imports OK, `py_compile` OK.

### FIX‑B — `tenant_service.py` never seeded `CompanySettings`
`CompanySettings(...)` was constructed with `short_name=_short`, but `CompanySettings` has **no `short_name` column** (that column is on the `Tenant` model). The resulting `TypeError` was swallowed by a broad `except (TypeError, AttributeError)`, so **`CompanySettings` was never seeded** for new tenants — they silently fell back to the default `DTC/786/` file prefix.
**Fix:** removed the invalid `short_name=` kwarg (kept `_short`, still used for `file_prefix=_default_file_prefix_for(slug, _short)`). Verified: a new tenant now seeds `file_prefix` (e.g. `NEWOEP/`) cleanly with no `TypeError`; `py_compile` OK.

---

## 6. Files Changed

| File | Change |
|---|---|
| `app/frontend/index.html` | FIX 1 — self‑hosted Chart.js `<script>` |
| `app/frontend/src/lib/chart.ts` | FIX 1 — removed CDN injection; poll `window.Chart` |
| `app/frontend/src/components/AssignWizard.tsx` | FIX 2 — 7 steps + wizard draft/resume API |
| `app/frontend/src/pages/DemandDetail.tsx` | FIX 2 — comment (5→7 step) |
| `app/api/endpoints/candidates.py` | FIX 2 — slot‑capacity guard in `finalize` |
| `app/services/pdf_engine.py` | FIX 3 — 10 `print()` → `dtc.pdf_engine` |
| `app/services/letterhead_renderer.py` | FIX 3 — 4 `print()` → `dtc.letterhead_renderer` |
| `app/services/overlay_engine.py` | FIX 3 — 1 `print()` → `dtc.overlay_engine` |
| `app/api/endpoints/documents.py` | FIX 3 — 2 `print()` → `dtc.documents` |
| `app/services/dtcbot_llm.py` | FIX‑A — corrected import |
| `app/services/tenant_service.py` | FIX‑B — removed invalid `short_name=` kwarg |
| `tests/test_candidate_wizard.py` | **new** — 4 wizard‑flow tests |
| `app/static/spa/*` | rebuilt SPA bundle |

---

## 7. Final Status

| Gate | Result |
|---|---|
| `pytest` | ✅ **92 passed** (88 baseline + 4 new) |
| `py_compile` (8 changed `.py`) | ✅ 0 errors |
| `tsc -b --noEmit` | ✅ 0 errors |
| Vite `npm run build` | ✅ OK → `app/static/spa/` |
| CSP violations at `/dashboard` | ✅ none |
| Backend ↔ frontend wizard step parity | ✅ 7 == 7 |

**Note on distribution (unchanged from prior review):** before external hand‑off, the shipped SQLite DB should be sanitised/reset so no real tenant PII travels in the archive. This is a packaging step, not a code defect, and is out of scope for these three code fixes.
