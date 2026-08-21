// ---------------------------------------------------------------------------
// Shared reference-list cache (filter dropdowns, wizard pickers).
//
// WHY THIS EXISTS
// Several pages need the same reference lists to fill their filter dropdowns
// and form selects: clients, demands, visa categories, embassies, cities.
// Before this module every page fetched them again on every mount, using the
// FULL row serializer:
//
//     /candidates mount   /clients/?limit=500      299 KB
//                         /demands/?limit=500      490 KB
//                         /lookups/visa-categories  37 KB
//                         --------------------------------
//                                                  826 KB  (~300 ms)
//
// ...to populate three pickers that only ever read a value and a label. The
// actual page data on that same mount is 29 KB. Navigating Candidates →
// Demands → Candidates paid that cost three times over.
//
// TWO FIXES, BOTH WIRED HERE
// 1. `compact=true` — an opt-in server-side projection on /clients/ and
//    /demands/ returning only id + display label (SELECT of two columns, no
//    joinedload, no per-row aggregation). Measured 789 KB -> 66 KB.
// 2. This cache — one request per list per session, shared between concurrent
//    callers, so repeat navigation refetches nothing.
//
// Measured together: 826 KB -> ~103 KB on a cold mount, ~0 KB on return visits.
//
// CACHE SEMANTICS (deliberately conservative)
//  * In-memory only. A page reload — which is what both logout and tenant
//    switching do — starts empty, so no cross-tenant or cross-user leakage is
//    possible. Nothing is persisted to localStorage/sessionStorage.
//  * Soft TTL. Reference lists change rarely (a new client, a new visa
//    category); at most TTL_MS of staleness is acceptable, and any list the
//    user just mutated is invalidated explicitly (see invalidateOptions).
//  * In-flight dedupe. Components mounting in the same tick share one request
//    instead of racing.
//  * Failures are NOT cached, so a dropped connection never leaves a page
//    permanently empty — the next mount retries.
//
// VALUE SEMANTICS — IMPORTANT
// The existing pages do not agree on what a dropdown's *value* is, and that is
// load-bearing on the wire:
//   * clients / demands  -> numeric id   (`?clients=3,9`, `client_id: 3`)
//   * visa categories    -> the NAME     (`?categories=Driver,Mason`)
//   * embassies / cities -> the NAME     (`<option value={e.name}>`)
// So this module caches the RAW rows and offers both projections. Callers pick
// the one their endpoint already expects; nothing about the request or the
// submitted payload changes.
// ---------------------------------------------------------------------------
import { api } from './api'

/** A dropdown entry. `value` is whatever the consuming endpoint expects. */
export interface Option {
  value: string
  label: string
}

/** Normalized shape every reference list is reduced to before caching. */
export interface OptionRow {
  id: number
  name: string
}

/** Cache keys — one per reference list. */
export type OptionKey = 'clients' | 'demands' | 'visaCategories' | 'embassies' | 'cities'

/** Reference lists are edited rarely; five minutes of staleness is acceptable
 * and still guarantees a long editing session eventually sees new rows. */
const TTL_MS = 5 * 60 * 1000

interface Entry {
  at: number
  rows: OptionRow[]
}

const cache = new Map<OptionKey, Entry>()
const inflight = new Map<OptionKey, Promise<OptionRow[]>>()

/** Loader per list. Each reduces the endpoint's wire shape to OptionRow[] so
 * callers never depend on the serializer of a particular endpoint. */
const loaders: Record<OptionKey, () => Promise<OptionRow[]>> = {
  // compact=true -> {id, company_name} only (299 KB -> 29 KB).
  clients: async () => {
    const { data } = await api.get('/clients/', { params: { limit: 500, compact: true } })
    return (data.items || []).map((c: any) => ({
      id: Number(c.id),
      name: c.company_name || `Client #${c.id}`,
    }))
  },
  // compact=true -> {id, file_number, file_number_display} only (490 KB -> 37 KB).
  demands: async () => {
    const { data } = await api.get('/demands/', { params: { limit: 500, compact: true } })
    return (data.items || []).map((d: any) => ({
      id: Number(d.id),
      name: d.file_number_display || d.file_number || `Demand #${d.id}`,
    }))
  },
  // Lookup tables are already small (37 KB / 1.4 KB / 5.6 KB) — they need the
  // caching half of this module, not a projection. Inactive categories are
  // filtered out here, exactly as the pages did inline before.
  visaCategories: async () => {
    const { data } = await api.get('/lookups/visa-categories', { params: { limit: 500 } })
    return (data.items || [])
      .filter((v: any) => v.is_active !== false)
      .map((v: any) => ({ id: Number(v.id), name: String(v.name) }))
  },
  embassies: async () => {
    const { data } = await api.get('/lookups/embassies', { params: { limit: 200 } })
    return (data.items || []).map((e: any) => ({ id: Number(e.id), name: String(e.name) }))
  },
  cities: async () => {
    const { data } = await api.get('/lookups/cities', { params: { limit: 300 } })
    return (data.items || []).map((c: any) => ({ id: Number(c.id), name: String(c.name) }))
  },
}

/**
 * Fetch a reference list as raw rows, served from cache when fresh.
 *
 * Concurrent callers for the same key share a single request. Rejections are
 * not cached — the promise is removed from `inflight` so the next call retries.
 */
export function getRows(key: OptionKey): Promise<OptionRow[]> {
  const hit = cache.get(key)
  if (hit && Date.now() - hit.at < TTL_MS) return Promise.resolve(hit.rows)

  const pending = inflight.get(key)
  if (pending) return pending

  const p = loaders[key]()
    .then((rows) => {
      cache.set(key, { at: Date.now(), rows })
      return rows
    })
    .finally(() => {
      inflight.delete(key)
    })
  inflight.set(key, p)
  return p
}

/** Rows projected to {value: id, label: name} — for clients/demands filters
 * and any select that submits a foreign key. */
export async function getIdOptions(key: OptionKey): Promise<Option[]> {
  const rows = await getRows(key)
  return rows.map((r) => ({ value: String(r.id), label: r.name }))
}

/** Rows projected to {value: name, label: name} — for the lookup selects that
 * store a free-text name (visa categories, embassies, cities). */
export async function getNameOptions(key: OptionKey): Promise<Option[]> {
  const rows = await getRows(key)
  return rows.map((r) => ({ value: r.name, label: r.name }))
}

/** Warm the cache without caring about the result — safe to call on idle. */
export function prefetchOptions(...keys: OptionKey[]): void {
  keys.forEach((k) => {
    getRows(k).catch(() => {
      /* prefetch is best-effort; the real consumer will retry and surface it */
    })
  })
}

/**
 * Drop cached lists so the next consumer refetches. Call this after creating,
 * renaming or deleting one of the underlying records — that keeps the cache
 * correct without a global store or polling. No argument clears everything.
 */
export function invalidateOptions(...keys: OptionKey[]): void {
  if (!keys.length) {
    cache.clear()
    return
  }
  keys.forEach((k) => cache.delete(k))
}

/** Wipe everything, including in-flight bookkeeping. Belt-and-braces for
 * logout / tenant switch (a reload already clears module state). */
export function clearOptionsCache(): void {
  cache.clear()
  inflight.clear()
}
