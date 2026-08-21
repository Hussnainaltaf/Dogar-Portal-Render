# Tenant Isolation & Document Coordinate Engine — Deep Audit Report

> **📁 ARCHIVED — point-in-time record.** This report describes the repository as it stood at the time it was written.
> Paths, filenames, counts and version numbers in it are deliberately **not**
> refreshed as the code moves on, so the document stays an honest account of
> what was actually found and fixed then. Some assets it names have since been
> renamed or relocated. **For the current state of the system, read
> `PROJECT_DOCUMENTATION.md` and `README.md` at the project root — not this file.**

**Date**: 2026-08-05  
**Scope**: Multi-tenant data isolation verification + PDF overlay coordinate engine review  
**Status**: PASS (both audits) — 1 performance fix applied

---

## Part 1: Tenant Isolation Audit

### Architecture Summary

| Layer | Mechanism | Status |
|-------|-----------|--------|
| Database | Per-tenant SQLite files (`data/tenants/tenant_<slug>.db`) | ✅ Isolated |
| Middleware | `TenantResolverMiddleware` resolves tenant from Host / `/t/<slug>/` path | ✅ Enforced |
| Session Factory | `get_tenant_db()` yields session bound to resolved tenant's DB | ✅ Scoped |
| JWT Binding | Token `tnt` claim must match resolved tenant slug | ✅ Cross-tenant reuse blocked |
| Engine Cache | Thread-safe `_ENGINES` dict keyed by db_path or `pg:{slug}` | ✅ No collision |
| Control Plane | Only `tenants.py` uses `get_db()` (control DB), gated by `_admin_only()` | ✅ Proper separation |
| Hard Failure | `TenantDatabaseMissing` → 503 (no fallback to control DB) | ✅ Leak-proof |

### Endpoint Verification (All 19 Routers)

| Endpoint File | DB Dependency | Isolation |
|---------------|--------------|-----------|
| candidates.py | `get_tenant_db` | ✅ |
| demands.py | `get_tenant_db` | ✅ |
| documents.py | `get_tenant_db` | ✅ |
| clients.py | `get_tenant_db` | ✅ |
| users.py | `get_tenant_db` | ✅ |
| dashboard.py | `get_tenant_db` | ✅ |
| backups.py | `get_tenant_db` | ✅ |
| auth.py | `get_tenant_db` | ✅ |
| settings.py | `get_tenant_db` | ✅ |
| messaging.py | `get_tenant_db` | ✅ |
| chatbot.py | `get_tenant_db` | ✅ |
| protector_letters.py | `get_tenant_db` | ✅ |
| lookups.py | `get_tenant_db` | ✅ |
| reminders.py | `get_tenant_db` | ✅ |
| form_schemas.py | `get_tenant_db` | ✅ |
| realtime.py | `get_tenant_db` | ✅ |
| ocr.py | No DB (stateless) | ✅ N/A |
| **tenants.py** | **`get_db` (control)** | ✅ Admin-only, permission-gated |

### Findings

#### ✅ PASS — No Cross-Tenant Data Leakage Vectors Found

1. **Every data endpoint** correctly uses `get_tenant_db()` — queries are physically confined to the tenant's own database file.
2. **JWT tenant claim** (`tnt`) is validated on every authenticated request in `deps.py`. A token issued for tenant A cannot access tenant B's data.
3. **Cache keys** include tenant slug prefix (e.g., `cands:{slug}:{user_id}:...`), preventing cross-tenant cache poisoning.
4. **Backup downloads** are scoped by filename prefix check (`portal-backup-{slug}-*`).
5. **Asset exports** in backups only include files from the tenant's own subdirectory.

#### ⚠️ LOW RISK — Shared File Upload Directory

- **Finding**: `UPLOAD_DIR/photos/` and `UPLOAD_DIR/avatars/` are single global directories shared across tenants.
- **Mitigations already in place**:
  - Photo filenames use random UUIDs (`cand_{uuid4_hex}.jpg`) — enumeration is infeasible
  - No directory listing endpoint exists
  - Files are served via direct filename reference from DB records (which are tenant-scoped)
- **Risk**: Negligible. An attacker would need to guess a 12-character hex UUID to access another tenant's photo.
- **Recommendation**: For future hardening, consider adding tenant slug as a subdirectory (e.g., `photos/{slug}/cand_xxx.jpg`). Low priority.

#### ✅ FIXED — Cache Invalidation Now Tenant-Scoped

- **Before**: `delete_pattern("cands:*")` flushed ALL tenants' candidate caches on any single tenant's write. Same for demands.
- **After**: `delete_pattern(f"cands:{tenant_slug}:*")` — only the current tenant's cache is invalidated.
- **Impact**: Performance improvement for multi-tenant deployments. Not a security issue, but a correctness fix.
- **Files modified**: `candidates.py` (3 calls), `demands.py` (3 calls)

---

## Part 2: Document Coordinate Engine Audit

### Architecture

```
DocumentTemplate → page_width, page_height, background_image, orientation
     └── DocumentField[] → x, y, width, height, font_size, field_type, field_key, meta
                                    ↓
              overlay_engine.py: resolve field keys → values
                                    ↓
              pdf_engine.py: render values at (x,y) coordinates on PDF canvas
```

### Coordinate System

- **Designer (frontend)**: Origin at **top-left**, Y increases downward
- **PDF (ReportLab)**: Origin at **bottom-left**, Y increases upward
- **Parity fix**: `text_baseline_y = y + max(h - font_size, 0.0)` — ensures designer positioning matches PDF output exactly

### Verification Results

| Component | Status | Notes |
|-----------|--------|-------|
| Aspect-lock on background upload | ✅ | `page_height` snapped to image aspect ratio automatically |
| Coordinate parity (designer ↔ PDF) | ✅ | `text_baseline_y` formula correctly mirrors designer |
| Height fallback (PDF = print = designer) | ✅ | Consistent: `field.height or (barcode→32, text→max(fs*1.25,14))` |
| Background fit modes (fill/contain/actual) | ✅ | Fill stretches, contain scales preserving ratio, actual = 1:1 |
| Path traversal protection | ✅ | `os.path.basename()` strips all path prefixes |
| Off-page field detection | ✅ | Logged as warnings, doesn't crash |
| Photo crop/fit | ✅ | Cover mode with center-crop, correct aspect math |
| Character cells (CNIC grids) | ✅ | Properly handles `group_sizes` for spacing |
| Barcode rendering | ✅ | Auto-scaling, QR support, SVG preview cache |
| Trade table | ✅ | Grows downward correctly from anchor position |
| Multi-page backgrounds | ✅ | Per-page background selection works |
| Field resolver (overlay_engine) | ✅ | Complete alias system, precedence rules correct |
| CompanySettings cache | ✅ | Keyed by DB bind URL (tenant-safe, 60s TTL) |
| Image/barcode caches | ✅ | LRU caches (32/128/512 entries), keyed by content hash |

### Conclusion

**No remaining bugs, mismatches, or fit/size issues found** in the coordinate engine. The prior audit fixes (coordinate parity, aspect-lock, height fallback parity) are all correctly implemented and consistent across:
- Frontend designer (React drag-and-drop)
- Print preview (HTML overlay)
- PDF generation (ReportLab canvas)

---

## Summary of Changes Made

| File | Change |
|------|--------|
| `app/api/endpoints/candidates.py` | Cache invalidation scoped to tenant slug (3 calls) |
| `app/api/endpoints/demands.py` | Cache invalidation scoped to tenant slug (3 calls) |

**Total lines changed**: ~12 (6 cache calls converted from global to tenant-scoped)

---

## Recommendations for Future Hardening

1. **File upload isolation** (Low priority): Add tenant slug subdirectory to photo/avatar upload paths
2. **Cache backend**: Consider Redis with key prefixes for production multi-instance deployments
3. **Rate limiting**: Per-tenant rate limits on write endpoints to prevent cache-thrashing attacks
4. **Audit log rotation**: Consider per-tenant audit log tables or partitioning for large deployments
