import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useSearchParams, Link } from 'react-router-dom'
import { api, apiError } from '@/lib/api'
import { getIdOptions, getNameOptions } from '@/lib/options'
import { useToast } from '@/components/Toast'
import { useReconnect } from '@/lib/useReconnect'
import { useLiveStatus } from '@/lib/useLiveStatus'
import { SendMessageModal } from '@/components/SendMessageModal'
import { printInFrame } from '@/lib/print'
import { humanize } from '@/lib/format'
import { lockBodyScroll, unlockBodyScroll } from '@/lib/scrollLock'

// ---------------------------------------------------------------------------
// Types — mirror the backend candidate _to_dict(with_context) shape.
// ---------------------------------------------------------------------------
export interface Candidate {
  id: number
  full_name: string
  name_arabic?: string
  mother_name?: string
  father_name?: string
  father_name_arabic?: string
  gender?: string
  marital_status?: string
  religion?: string
  date_of_birth?: string
  place_of_birth?: string
  nationality?: string
  address?: string
  phone?: string
  email?: string
  tehsil?: string
  district?: string
  province?: string
  photo?: string
  passport_no?: string
  passport_issue_date?: string
  passport_expiry_date?: string
  issuing_authority?: string
  passport_issue_place?: string
  cnic?: string
  qualification?: string
  age_employee?: number | null
  profession?: string
  salary?: number
  next_of_kin_name?: string
  next_of_kin_nic?: string
  next_of_kin_relation?: string
  e_number?: string
  destination?: string
  status?: string
  status_label?: string
  status_classes?: string
  notes?: string
  client_name?: string
  client_id?: number
  demand_file_no?: string
  demand_file_no_display?: string
  demand_id?: number
  category?: string
  embassy?: string
  country?: string
  sponsor_name?: string
  trade?: string
  created_at?: string
  updated_at?: string
  [k: string]: unknown
}

interface DocTemplate {
  id: number
  name: string
  description?: string
  category?: string
  data_source?: string
}

// Workflow stages (matches backend all_stages order).
const WORKFLOW = [
  { value: 'new', label: 'Received', icon: 'fa-inbox' },
  { value: 'docs_pending', label: 'Documents', icon: 'fa-folder-open' },
  { value: 'medical', label: 'Medical', icon: 'fa-stethoscope' },
  { value: 'protector', label: 'Protector', icon: 'fa-stamp' },
  { value: 'e_number', label: 'E-Number', icon: 'fa-barcode' },
  { value: 'visa_stamping', label: 'Visa', icon: 'fa-passport' },
  { value: 'travel_ready', label: 'Clearance', icon: 'fa-plane-departure' },
  { value: 'deployed', label: 'Departed', icon: 'fa-plane' },
]

const STAGE_OPTIONS = WORKFLOW.map((w) => ({ value: w.value, label: w.label }))
type FilterOption = { value: string; label: string }

function MultiFilter({ label, options, values, onChange }: { label: string; options: FilterOption[]; values: string[]; onChange: (values: string[]) => void }) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const root = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const close = (event: MouseEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', close); return () => document.removeEventListener('mousedown', close)
  }, [])
  const visible = useMemo(() => options.filter((o) => o.label.toLowerCase().includes(search.toLowerCase())).slice(0, 100), [options, search])
  return <div ref={root} className="relative">
    <button type="button" aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen((v) => !v)} className={`min-w-[145px] rounded-lg border px-3 py-2 text-left text-sm ${values.length ? 'border-blue-300 bg-blue-50 text-blue-800' : 'border-slate-200 bg-white text-slate-600'}`}>
      <span>{values.length ? `${label} (${values.length})` : label}</span><i className="fa-solid fa-chevron-down float-right mt-1 text-[10px]" />
    </button>
    {open && <div className="absolute left-0 z-40 mt-1 w-64 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-xl">
      <div className="border-b border-slate-100 p-2"><input autoFocus value={search} onChange={(e) => setSearch(e.target.value)} placeholder={`Search ${label.toLowerCase()}…`} className="form-input w-full text-sm" /></div>
      <div role="listbox" aria-multiselectable="true" className="max-h-60 overflow-y-auto p-1">
        {visible.map((option) => { const checked = values.includes(option.value); return <label key={option.value} className="flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 text-sm hover:bg-blue-50">
          <input type="checkbox" checked={checked} onChange={() => onChange(checked ? values.filter((v) => v !== option.value) : [...values, option.value])} />
          <span className="truncate">{option.label}</span>
        </label> })}
        {!visible.length && <p className="px-3 py-4 text-center text-xs text-slate-400">No matches</p>}
      </div>
    </div>}
  </div>
}

function pillClass(c?: Candidate): string {
  return c?.status_classes || 'bg-slate-100 text-slate-700'
}

/** Resolve a never-raw, human-friendly status label for a candidate.
 *  Order: backend-provided label → WORKFLOW map → humanized slug → fallback. */
function statusLabel(c?: Candidate, fallback = '—'): string {
  if (c?.status_label) return c.status_label
  const slug = c?.status
  if (!slug) return fallback
  const w = WORKFLOW.find((x) => x.value === slug)
  if (w) return w.label
  return humanize(slug) || fallback
}

// ---------------------------------------------------------------------------
// Candidates list page
// ---------------------------------------------------------------------------
export default function Candidates() {
  const toast = useToast()
  const [params, setParams] = useSearchParams()
  const [rows, setRows] = useState<Candidate[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [q, setQ] = useState('')
  const [stages, setStages] = useState<string[]>([])
  const [clientIds, setClientIds] = useState<string[]>([])
  const [demandIds, setDemandIds] = useState<string[]>([])
  const [categories, setCategories] = useState<string[]>([])
  const [clientOptions, setClientOptions] = useState<FilterOption[]>([])
  const [demandOptions, setDemandOptions] = useState<FilterOption[]>([])
  const [categoryOptions, setCategoryOptions] = useState<FilterOption[]>([])
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(15)
  const [openId, setOpenId] = useState<number | null>(null)
  // Bumped whenever the app reconnects so the open side sheet refetches too.
  const [reconnectKey, setReconnectKey] = useState(0)
  const debounce = useRef<number | undefined>(undefined)

  const load = useCallback(() => {
    setLoading(true)
    const p: Record<string, string> = { limit: String(pageSize), skip: String((page - 1) * pageSize) }
    if (q.trim()) p.q = q.trim()
    if (stages.length) p.statuses = stages.join(',')
    if (clientIds.length) p.clients = clientIds.join(',')
    if (demandIds.length) p.demands = demandIds.join(',')
    if (categories.length) p.categories = categories.join(',')
    api.get('/candidates/', { params: p })
      .then(({ data }) => {
        setRows(Array.isArray(data) ? data : data.items || [])
        setTotal((Array.isArray(data) ? data.length : data.total) || 0)
      })
      .catch((e) => toast.show(apiError(e, 'Failed to load candidates'), 'error'))
      .finally(() => setLoading(false))
  }, [q, stages, clientIds, demandIds, categories, page, pageSize, toast])

  // Filter dropdown data. Served by the shared reference-list cache
  // (lib/options.ts): one compact request per list per session instead of
  // 826 KB of full rows re-fetched on every mount. Values keep their existing
  // wire semantics — ids for clients/demands, names for categories — so the
  // query string this page builds is unchanged.
  useEffect(() => {
    let alive = true
    Promise.all([
      getIdOptions('clients'),
      getIdOptions('demands'),
      getNameOptions('visaCategories'),
    ]).then(([clients, demands, categories]) => {
      if (!alive) return
      setClientOptions(clients)
      setDemandOptions(demands)
      setCategoryOptions(categories)
    }).catch(() => { /* Filters remain usable with status/search if lookups fail. */ })
    return () => { alive = false }
  }, [])

  useEffect(() => {
    window.clearTimeout(debounce.current)
    debounce.current = window.setTimeout(load, 250)
    return () => window.clearTimeout(debounce.current)
  }, [load])

  // On reconnect (offline→online, or tab refocus with a live connection),
  // refetch the list immediately and force the open side sheet to refetch — so
  // the user never sees rows/details cached while the connection was down.
  const { online } = useReconnect(useCallback(() => {
    load()
    setReconnectKey((k) => k + 1)
  }, [load]))

  // Live status: refetch the list whenever any candidate/assignment/demand/
  // document status changes on the server. Uses SSE with automatic polling
  // fallback so statuses stay fresh even if the realtime stream drops.
  useLiveStatus(useCallback(() => {
    load()
    setReconnectKey((k) => k + 1)
  }, [load]))

  // Deep-link ?open=<id>
  useEffect(() => {
    const open = params.get('open')
    if (open) setOpenId(Number(open))
  }, [params])

  const closeDrawer = () => {
    setOpenId(null)
    const next = new URLSearchParams(params)
    next.delete('open')
    setParams(next, { replace: true })
  }

  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const hasFilters = !!(q || stages.length || clientIds.length || demandIds.length || categories.length)

  return (
    <div className="space-y-5">
      {!online && (
        <div role="status" data-testid="offline-banner" className="flex items-center gap-2 text-[13px] text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
          <i className="fa-solid fa-wifi text-amber-500" />
          <span>You’re offline — showing the last loaded data. It will refresh automatically when you reconnect.</span>
        </div>
      )}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-[22px] font-bold text-slate-800 flex items-center gap-2">
            <i className="fa-regular fa-id-badge text-blue-600" /> Candidates
          </h1>
          <p className="text-sm text-slate-500 mt-0.5">All registered candidates, most recent first.</p>
        </div>
      </div>

      {/* Filter strip */}
      <div className="card">
        <div className="card-header flex-wrap gap-y-2 flex items-center justify-between">
          <div className="flex items-center gap-2 flex-wrap">
            <div className="relative">
              <i className="fa-solid fa-search absolute left-3 top-2.5 text-slate-400 text-xs" />
              <input value={q} onChange={(e) => { setQ(e.target.value); setPage(1) }}
                placeholder="Search name, CNIC, passport..."
                className="pl-9 pr-3 py-2 text-sm border border-slate-200 rounded-lg w-64 focus:outline-none focus:border-blue-500" />
            </div>
            <MultiFilter label="Clients" options={clientOptions} values={clientIds} onChange={(v) => { setClientIds(v); setPage(1) }} />
            <MultiFilter label="Demand files" options={demandOptions} values={demandIds} onChange={(v) => { setDemandIds(v); setPage(1) }} />
            <MultiFilter label="Categories" options={categoryOptions} values={categories} onChange={(v) => { setCategories(v); setPage(1) }} />
            <MultiFilter label="Statuses" options={STAGE_OPTIONS} values={stages} onChange={(v) => { setStages(v); setPage(1) }} />
            {hasFilters && (
              <button onClick={() => { setQ(''); setStages([]); setClientIds([]); setDemandIds([]); setCategories([]); setPage(1) }}
                className="text-xs text-slate-500 hover:text-rose-600 px-2 py-1"><i className="fa-solid fa-xmark" /> Clear</button>
            )}
          </div>
          <span className="text-xs text-slate-500">{total.toLocaleString()} total</span>
        </div>

        <div className="overflow-x-auto table-scroll">
          <table className="data-table responsive-cards w-full">
            <thead>
              <tr>
                <th>Name</th>
                <th>CNIC</th>
                <th>Passport</th>
                <th>Client</th>
                <th>Demand File</th>
                <th>Embassy</th>
                <th>Category</th>
                <th>Status</th>
                <th className="w-20 text-center">Actions</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={9} className="text-center py-12 text-slate-400"><i className="fa-solid fa-spinner fa-spin text-xl text-blue-600" /></td></tr>
              ) : rows.length === 0 ? (
                <tr><td colSpan={9} className="text-center py-12 text-slate-400"><i className="fa-regular fa-folder-open text-3xl block mb-2 opacity-50" />No candidates found</td></tr>
              ) : rows.map((c) => (
                <tr key={c.id} className="cursor-pointer hover:bg-slate-50 focus:bg-blue-50 focus:outline focus:outline-2 focus:outline-blue-400"
                    tabIndex={0} role="button" aria-label={`Open candidate ${c.full_name}`}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setOpenId(c.id) } }}
                    onClick={() => setOpenId(c.id)}>
                  <td data-label="Name" data-primary>
                    <div className="font-semibold text-slate-800">{c.full_name}</div>
                    {c.father_name && <div className="text-xs text-slate-400">S/O {c.father_name}</div>}
                  </td>
                  <td data-label="CNIC" className="font-mono text-[12px]">{c.cnic || '—'}</td>
                  <td data-label="Passport" className="font-mono text-[12px]">{c.passport_no || '—'}</td>
                  <td data-label="Client" onClick={(e) => e.stopPropagation()}>
                    {c.client_name
                      ? (c.client_id
                          ? <Link to={`/clients/${c.client_id}`} className="text-blue-600 hover:text-blue-700 hover:underline font-semibold">{c.client_name}</Link>
                          : <span className="text-slate-700">{c.client_name}</span>)
                      : <span className="text-slate-400">—</span>}
                  </td>
                  <td data-label="Demand File" onClick={(e) => e.stopPropagation()}>
                    {(c.demand_file_no_display || c.demand_file_no)
                      ? (c.demand_id
                          ? <Link to={`/demands/${c.demand_id}`} className="font-mono text-[12px] text-blue-600 hover:text-blue-700 hover:underline font-semibold">{c.demand_file_no_display || c.demand_file_no}</Link>
                          : <span className="font-mono text-[12px] text-slate-700">{c.demand_file_no_display || c.demand_file_no}</span>)
                      : <span className="text-slate-400">—</span>}
                  </td>
                  <td data-label="Embassy">
                    {c.embassy
                      ? <span className={`px-2 py-0.5 rounded-full text-[11px] font-semibold ${/karachi/i.test(String(c.embassy)) ? 'bg-teal-50 text-teal-700' : /islamabad/i.test(String(c.embassy)) ? 'bg-indigo-50 text-indigo-700' : 'bg-slate-100 text-slate-600'}`}>{String(c.embassy)}</span>
                      : <span className="text-slate-400">—</span>}
                  </td>
                  <td data-label="Category">{c.category || c.trade || c.profession || '—'}</td>
                  <td data-label="Status"><span className={`px-2 py-0.5 rounded-full text-[11px] font-semibold ${pillClass(c)}`}>{statusLabel(c)}</span></td>
                  <td data-label="Actions" className="text-center" onClick={(e) => e.stopPropagation()}>
                    <button onClick={() => setOpenId(c.id)} className="inline-flex items-center justify-center w-9 h-9 rounded-lg text-blue-600 hover:text-blue-700 hover:bg-blue-50 transition" title="View"><i className="fa-regular fa-eye" /></button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        <div className="px-4 py-3 border-t border-slate-100 flex items-center justify-between text-xs text-slate-500">
          <div>{total.toLocaleString()} total · Page {page} of {totalPages}</div>
          <div className="flex items-center gap-2">
            <select value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1) }}
              className="border border-slate-200 rounded px-2 py-1 text-xs">
              {[10, 15].map((n) => <option key={n} value={n}>{n} per page</option>)}
            </select>
            <button disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}
              className="px-3 py-1 border border-slate-200 rounded hover:bg-slate-50 disabled:opacity-40">Prev</button>
            <button disabled={page >= totalPages} onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              className="px-3 py-1 border border-slate-200 rounded hover:bg-slate-50 disabled:opacity-40">Next</button>
          </div>
        </div>
      </div>

      {openId != null && <CandidateDrawer id={openId} onClose={closeDrawer} refreshKey={reconnectKey} />}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Candidate detail drawer (5 tabs: Details / Documents / Check List / Status / Activity)
// ---------------------------------------------------------------------------
type DTab = 'profile' | 'documents' | 'checklist' | 'status' | 'activity'

const DRAWER_TABS: [DTab, string, string][] = [
  ['profile', 'fa-regular fa-user', 'Details'],
  ['documents', 'fa-regular fa-file-lines', 'Documents'],
  ['checklist', 'fa-solid fa-list-check', 'Check List'],
  ['status', 'fa-solid fa-flag', 'Status'],
  ['activity', 'fa-regular fa-clock', 'Activity'],
]

export function CandidateDrawer({ id, onClose, refreshKey = 0 }: { id: number; onClose: () => void; refreshKey?: number }) {
  const toast = useToast()
  const [c, setC] = useState<Candidate | null>(null)
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState<DTab>('profile')
  const panelRef = useRef<HTMLElement | null>(null)
  const closeBtnRef = useRef<HTMLButtonElement | null>(null)
  const prevFocusRef = useRef<HTMLElement | null>(null)

  // Refetch when the id changes OR when refreshKey bumps (e.g. after the app
  // reconnects) so the side sheet never shows data cached while offline.
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api.get<Candidate>(`/candidates/${id}`)
      .then(({ data }) => { if (!cancelled) setC(data) })
      .catch(() => { if (!cancelled) toast.show('Could not load candidate details', 'error') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [id, refreshKey, toast])

  // Esc to close + lock body scroll + focus trap while the drawer is open
  useEffect(() => {
    prevFocusRef.current = document.activeElement as HTMLElement | null
    requestAnimationFrame(() => closeBtnRef.current?.focus())

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { onClose(); return }
      if (e.key !== 'Tab') return
      const root = panelRef.current
      if (!root) return
      const focusable = root.querySelectorAll<HTMLElement>(
        'a[href],button:not([disabled]),textarea,input,select,[tabindex]:not([tabindex="-1"])'
      )
      if (focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus() }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
    }
    window.addEventListener('keydown', onKey)
    lockBodyScroll()
    return () => {
      window.removeEventListener('keydown', onKey)
      unlockBodyScroll()
      prevFocusRef.current?.focus?.()
    }
  }, [onClose])

  // Roving arrow-key navigation across the tablist.
  const onTabKey = (e: React.KeyboardEvent, idx: number) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return
    e.preventDefault()
    const next = e.key === 'ArrowRight' ? (idx + 1) % DRAWER_TABS.length : (idx - 1 + DRAWER_TABS.length) % DRAWER_TABS.length
    setTab(DRAWER_TABS[next][0])
    const btns = panelRef.current?.querySelectorAll<HTMLElement>('[role="tab"]')
    btns?.[next]?.focus()
  }

  const embLow = (c?.embassy || '').toLowerCase()
  const isKhi = embLow.includes('karachi')
  const isIsb = embLow.includes('islamabad')

  // Rendered via portal to <body> so `fixed` positioning is always relative to
  // the viewport — immune to any ancestor transform/filter stacking context.
  return createPortal(
    <div className="fixed inset-0 z-[100]">
      <div className="absolute inset-0 bg-slate-900/50 backdrop-blur-sm animate-fade-in" onClick={onClose} />
      <aside ref={panelRef} role="dialog" aria-modal="true" aria-label={c?.full_name ? `Candidate details: ${c.full_name}` : 'Candidate details'}
        className="absolute top-0 right-0 h-full w-full md:w-[760px] max-w-full bg-white shadow-2xl flex flex-col animate-slide-in-right">
        {/* Header */}
        <div className="px-6 py-4 border-b border-slate-200 flex items-center justify-between bg-white shadow-sm">
          <div className="flex items-center gap-3 min-w-0">
            <div className="w-9 h-9 rounded-lg bg-blue-50 text-blue-600 flex items-center justify-center shrink-0">
              <i className="fa-solid fa-id-card-clip" />
            </div>
            <div className="min-w-0">
              <h3 className="font-bold text-slate-800 text-lg truncate">{c?.full_name || (loading ? 'Loading…' : 'Candidate')}</h3>
              <div className="flex items-center gap-2 flex-wrap mt-0.5">
                <p className="text-xs text-slate-500">{c?.father_name ? `S/O ${c.father_name}` : ''}</p>
                {c?.embassy && <span className={`px-2 py-0.5 text-[10px] rounded-full font-semibold ${isKhi || isIsb ? 'bg-blue-50 text-blue-700' : 'bg-slate-100 text-slate-700'}`}>{c.embassy}</span>}
                {c?.client_name && <span className="px-2 py-0.5 text-[10px] rounded-full font-semibold bg-slate-100 text-slate-700">{c.client_name}</span>}
                {(c?.demand_file_no_display || c?.demand_file_no) && <span className="px-2 py-0.5 text-[10px] rounded-full font-semibold bg-blue-50 text-blue-700 font-mono">{c.demand_file_no_display || c.demand_file_no}</span>}
              </div>
            </div>
          </div>
          <button ref={closeBtnRef} onClick={onClose} aria-label="Close candidate details (Escape)" className="w-8 h-8 rounded-lg text-slate-400 hover:text-slate-700 hover:bg-slate-100 flex items-center justify-center" title="Close (Esc)"><i className="fa-solid fa-xmark text-lg" /></button>
        </div>

        {/* Tab strip */}
        <div className="px-6 py-3 bg-white border-b border-slate-100">
          <div role="tablist" aria-label="Candidate sections" className="inline-flex bg-slate-100 rounded-lg p-1 gap-1 text-sm">
            {DRAWER_TABS.map(([key, icon, label], idx) => (
              <button key={key} role="tab" id={`ctab-${key}`} aria-controls={`cpanel-${key}`} aria-selected={tab === key}
                tabIndex={tab === key ? 0 : -1} onKeyDown={(e) => onTabKey(e, idx)} onClick={() => setTab(key)}
                className={`px-3 py-1.5 rounded-md font-semibold flex items-center gap-1.5 transition ${tab === key ? 'bg-white text-blue-700 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}>
                <i className={icon} /> {label}
              </button>
            ))}
          </div>
        </div>

        {/* Tab content */}
        <div role="tabpanel" id={`cpanel-${tab}`} aria-labelledby={`ctab-${tab}`} tabIndex={0} className="flex-1 overflow-y-auto bg-slate-50 relative">
          {loading ? (
            <div className="py-20 text-center text-slate-400"><i className="fa-solid fa-spinner fa-spin text-2xl text-blue-600" /></div>
          ) : !c ? (
            <div className="py-20 text-center text-slate-400">No data</div>
          ) : (
            <>
              {tab === 'profile' && <ProfileTab c={c} />}
              {tab === 'documents' && <DocumentsTab c={c} isKhi={isKhi} isIsb={isIsb} toast={toast} />}
              {tab === 'checklist' && <ChecklistTab candidateId={c.id} toast={toast} />}
              {tab === 'status' && <StatusTab c={c} />}
              {tab === 'activity' && <ActivityTab c={c} toast={toast} />}
            </>
          )}
        </div>
      </aside>
    </div>,
    document.body,
  )
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------
function Cell({ label, value, mono, rtl, span }: { label: string; value?: React.ReactNode; mono?: boolean; rtl?: boolean; span?: boolean }) {
  return (
    <div className={span ? 'sm:col-span-2' : ''}>
      <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-wide">{label}</div>
      <div className={`text-sm text-slate-800 mt-0.5 ${mono ? 'font-mono' : ''}`} dir={rtl ? 'rtl' : undefined}>{value || '—'}</div>
    </div>
  )
}

function InfoCard({ icon, iconBg, title, action, children }: { icon: string; iconBg: string; title: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="bg-white border border-slate-200 rounded-xl shadow-sm overflow-hidden">
      <div className="px-5 py-3 border-b border-slate-100 bg-slate-50 flex items-center gap-2">
        <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${iconBg}`}><i className={icon} /></div>
        <h4 className="font-semibold text-slate-800 text-sm">{title}</h4>
        {action}
      </div>
      <div className="px-4 sm:px-5 py-4 grid grid-cols-1 xs:grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-3">{children}</div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// PROFILE TAB
// ---------------------------------------------------------------------------
function ProfileTab({ c }: { c: Candidate }) {
  const photoUrl = c.photo ? `/static/uploads/photos/${c.photo}` : ''
  const [imgErr, setImgErr] = useState(false)
  const [msgOpen, setMsgOpen] = useState(false)
  const curStageIdx = WORKFLOW.findIndex((w) => w.value === c.status)

  return (
    <div>
      {/* Hero header */}
      <div className="relative bg-gradient-to-br from-slate-50 to-slate-100 border-b border-slate-200 px-6 py-6">
        <div className="absolute top-0 left-0 right-0 h-[3px] bg-gradient-to-r from-blue-500 via-blue-600 to-blue-500" />
        <div className="absolute top-4 right-4 flex items-center gap-2">
          <a href={`/candidates/${c.id}/print-profile?auto=1`} target="_blank" rel="noopener noreferrer"
            className="text-xs font-semibold bg-white hover:bg-slate-50 text-slate-700 border border-slate-200 px-3 py-1.5 rounded-lg flex items-center gap-1.5 shadow-sm">
            <i className="fa-solid fa-print" /> Print
          </a>
        </div>
        <div className="flex items-start gap-5">
          <div className="w-24 h-24 rounded-2xl bg-white flex items-center justify-center text-blue-700 text-3xl font-bold shadow-md overflow-hidden shrink-0 border border-slate-200 ring-4 ring-white">
            {photoUrl && !imgErr
              ? <img src={photoUrl} alt={`Photo of ${c.full_name}`} loading="lazy" decoding="async" className="w-full h-full object-cover" onError={() => setImgErr(true)} />
              : (c.full_name?.[0]?.toUpperCase() || '?')}
          </div>
          <div className="flex-1 min-w-0 pt-1">
            <h2 className="text-2xl font-bold truncate text-slate-800">{c.full_name}</h2>
            <div className="text-sm text-slate-500 mt-0.5">{c.father_name ? `S/O ${c.father_name}` : ''}</div>
            <div className="mt-3 flex flex-wrap gap-2 text-xs">
              <span className="px-2.5 py-1 bg-white border border-slate-200 text-slate-700 rounded-md font-mono font-semibold flex items-center gap-1.5 shadow-sm"><i className="fa-solid fa-hashtag text-slate-400" />{c.id}</span>
              {c.e_number && <span className="px-2.5 py-1 bg-amber-50 border border-amber-200 text-amber-800 rounded-md font-mono font-semibold flex items-center gap-1.5 shadow-sm"><i className="fa-solid fa-barcode" /> E# {c.e_number}</span>}
              {c.status_label && <span className="px-2.5 py-1 bg-emerald-50 border border-emerald-200 text-emerald-800 rounded-md font-semibold flex items-center gap-1.5 shadow-sm"><i className="fa-solid fa-flag-checkered" /> {c.status_label}</span>}
            </div>
          </div>
        </div>

        {/* Workflow tracker */}
        <div className="mt-6 bg-white border border-slate-200 rounded-lg p-3 shadow-sm">
          <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-2 font-semibold">Workflow Progress</div>
          <div className="flex items-center justify-between gap-1">
            {WORKFLOW.map((w, i) => {
              const done = curStageIdx >= 0 && i <= curStageIdx
              const cur = i === curStageIdx
              return (
                <div key={w.value} className="flex-1 flex flex-col items-center gap-1 min-w-0">
                  <div className={`w-7 h-7 rounded-full flex items-center justify-center text-[11px] ${cur ? 'bg-blue-600 text-white ring-2 ring-blue-200' : done ? 'bg-emerald-500 text-white' : 'bg-slate-100 text-slate-400'}`}>
                    <i className={`fa-solid ${w.icon}`} />
                  </div>
                  <div className={`text-[9px] text-center truncate w-full ${cur ? 'text-blue-700 font-semibold' : done ? 'text-emerald-600' : 'text-slate-400'}`}>{w.label}</div>
                </div>
              )
            })}
          </div>
        </div>
      </div>

      {/* Info cards */}
      <div className="p-6 space-y-4">
        <InfoCard icon="fa-solid fa-user text-blue-600" iconBg="bg-blue-100" title="Personal Information">
          <Cell label="Full Name" value={c.full_name} />
          <Cell label="Name (Arabic)" value={c.name_arabic} rtl />
          <Cell label="Father's Name" value={c.father_name} />
          <Cell label="Mother's Name" value={c.mother_name} />
          <Cell label="Date of Birth" value={c.date_of_birth} />
          <Cell label="Place of Birth" value={c.place_of_birth} />
          <Cell label="Gender" value={c.gender} />
          <Cell label="Marital Status" value={c.marital_status} />
          <Cell label="Nationality" value={c.nationality} />
          <Cell label="Religion" value={c.religion} />
          <Cell label="Qualification" value={c.qualification} />
          <Cell label="Age" value={c.age_employee != null ? String(c.age_employee) : ''} />
        </InfoCard>

        <InfoCard icon="fa-solid fa-passport text-blue-600" iconBg="bg-blue-100" title="Identity & Passport">
          <Cell label="CNIC" value={c.cnic} mono />
          <Cell label="Passport No." value={c.passport_no} mono />
          <Cell label="Passport Issue" value={c.passport_issue_date} />
          <Cell label="Passport Expiry" value={c.passport_expiry_date} />
          <Cell label="Issue Place" value={c.passport_issue_place} />
          <Cell label="Issuing Authority" value={c.issuing_authority} />
        </InfoCard>

        <InfoCard icon="fa-solid fa-location-dot text-emerald-600" iconBg="bg-emerald-100" title="Contact & Address"
          action={c.phone ? (
            <button
              type="button"
              onClick={() => setMsgOpen(true)}
              className="ml-auto inline-flex items-center gap-1.5 text-xs font-semibold text-emerald-700 hover:text-emerald-800 bg-emerald-50 hover:bg-emerald-100 border border-emerald-200 rounded-lg px-2.5 py-1 transition">
              <i className="fa-solid fa-paper-plane text-[11px]" /> Send message
            </button>
          ) : undefined}>
          <Cell label="Phone" value={c.phone} mono />
          <Cell label="Email" value={c.email} />
          <Cell label="Tehsil" value={c.tehsil} />
          <Cell label="District" value={c.district} />
          <Cell label="Province" value={c.province} />
          <Cell label="Full Address" value={c.address} span />
        </InfoCard>

        <InfoCard icon="fa-solid fa-people-roof text-amber-600" iconBg="bg-amber-100" title="Next of Kin">
          <Cell label="Name" value={c.next_of_kin_name} />
          <Cell label="CNIC" value={c.next_of_kin_nic} mono />
          <Cell label="Relationship" value={c.next_of_kin_relation} />
        </InfoCard>

        <InfoCard icon="fa-solid fa-folder-open text-blue-600" iconBg="bg-blue-100" title="Linked Demand File"
          action={c.demand_id ? <Link to={`/demands/${c.demand_id}`} className="ml-auto text-xs font-semibold text-blue-600 hover:underline">Open file <i className="fa-solid fa-arrow-up-right-from-square text-[10px] ml-0.5" /></Link> : undefined}>
          <Cell label="File Number" value={<span className="text-blue-700 font-mono">{c.demand_file_no_display || c.demand_file_no}</span>} />
          <Cell label="Client / Customer" value={c.client_name} />
          <Cell label="Sponsor" value={c.sponsor_name} />
          <Cell label="Country" value={c.country} />
          <Cell label="Embassy" value={c.embassy} />
        </InfoCard>

        <InfoCard icon="fa-solid fa-briefcase text-slate-700" iconBg="bg-slate-100 border border-slate-200" title="Visa Category">
          <Cell label="Trade / Profession" value={c.trade || c.profession} />
          <Cell label="Salary" value={c.salary ? `${c.salary}` : ''} />
          <Cell label="Destination" value={c.destination} />
        </InfoCard>
      </div>

      <SendMessageModal
        open={msgOpen}
        onClose={() => setMsgOpen(false)}
        to={c.phone}
        candidateId={c.id}
        candidateName={c.full_name}
      />
    </div>
  )
}

// ---------------------------------------------------------------------------
// DOCUMENTS TAB — Protector Documents + Visa Process (embassy ISB/KHI filtered)
// ---------------------------------------------------------------------------
function DocumentsTab({ c, isKhi, isIsb, toast }: { c: Candidate; isKhi: boolean; isIsb: boolean; toast: ReturnType<typeof useToast> }) {
  const [prot, setProt] = useState<DocTemplate[]>([])
  const [visa, setVisa] = useState<DocTemplate[]>([])
  const [loading, setLoading] = useState(true)
  const embShort = isKhi ? 'Karachi' : (isIsb ? 'Islamabad' : '')

  useEffect(() => {
    setLoading(true)
    // Embassy-based visa form availability is enforced SERVER-SIDE via the
    // ?embassy= param (single source of truth); the client keeps a defensive
    // filter as well so a stale cache can never show the wrong embassy form.
    const visaParams: Record<string, string> = { category: 'Visa Process' }
    if (embShort) visaParams.embassy = embShort
    Promise.all([
      api.get<DocTemplate[]>('/documents/templates', { params: { category: 'Protector Documents' } }),
      api.get<DocTemplate[]>('/documents/templates', { params: visaParams }),
    ])
      .then(([p, v]) => {
        setProt(p.data || [])
        let items = v.data || []
        if (embShort) {
          const mine = embShort.toLowerCase()
          const other = mine === 'karachi' ? 'islamabad' : 'karachi'
          items = items.filter((t) => !(t.name || '').toLowerCase().includes(other))
          items.sort((a, b) => {
            const am = (a.name || '').toLowerCase().includes(mine) ? 0 : 1
            const bm = (b.name || '').toLowerCase().includes(mine) ? 0 : 1
            return am - bm
          })
        }
        setVisa(items)
      })
      .catch((e) => toast.show(apiError(e, 'Failed to load documents'), 'error'))
      .finally(() => setLoading(false))
  }, [embShort, toast])

  // /print returns server-rendered HTML overlay; print via hidden iframe.
  const printDoc = (t: DocTemplate) => {
    const recId = t.data_source === 'demand' && c.demand_id ? c.demand_id : c.id
    printInFrame(`/api/documents/templates/${t.id}/print?record_id=${recId}`)
  }

  return (
    <div className="p-6 space-y-4">
      {/* Protector Documents */}
      <section className="bg-white border border-slate-200 rounded-xl overflow-hidden shadow-sm">
        <header className="px-4 py-3 bg-slate-800 text-white flex items-center justify-between">
          <h4 className="font-semibold text-[13px] tracking-wide uppercase flex items-center gap-2"><i className="fa-solid fa-stamp text-slate-300" />Protector Documents</h4>
          <span className="text-[11px] font-medium bg-white/15 px-2.5 py-0.5 rounded-full">{prot.length}</span>
        </header>
        <DocList items={prot} loading={loading} color="emerald" onPrint={printDoc} />
      </section>

      {/* Visa Process */}
      <section className="bg-white border border-slate-200 rounded-xl overflow-hidden shadow-sm">
        <header className="px-4 py-3 bg-blue-800 text-white flex items-center justify-between">
          <h4 className="font-semibold text-[13px] tracking-wide uppercase flex items-center gap-2"><i className="fa-solid fa-passport text-blue-200" />Visa Process</h4>
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-medium bg-white/15 px-2.5 py-0.5 rounded-full">{c.embassy ? c.embassy : 'No embassy'}</span>
            <span className="text-[11px] font-medium bg-white/15 px-2.5 py-0.5 rounded-full">{visa.length + 1}</span>
          </div>
        </header>
        {/* E-Barcode synthetic row (auto-generated from E-Number + passport, prints on blank page) */}
        <div className="divide-y divide-slate-100">
          <div className="flex items-center justify-between px-4 py-3.5 hover:bg-slate-50 transition-colors">
            <div className="min-w-0">
              <div className="font-semibold text-sm text-slate-800 truncate">E-Barcode</div>
              <div className="text-[11px] text-slate-400 mt-0.5">E-Number {c.e_number || '—'} · Passport {c.passport_no || '—'}</div>
            </div>
            <button onClick={() => printInFrame(`/api/protector-letters/e-barcode/${c.id}`)}
              className="px-3.5 py-1.5 bg-blue-700 hover:bg-blue-800 active:bg-blue-900 text-white text-xs font-semibold rounded-md inline-flex items-center gap-1.5 transition-colors">
              <i className="fa-solid fa-print" /> Print
            </button>
          </div>
          <DocList items={visa} loading={loading} color="indigo" onPrint={printDoc} embedded />
        </div>
      </section>
    </div>
  )
}

function DocList({ items, loading, color, onPrint, embedded }: { items: DocTemplate[]; loading: boolean; color: string; onPrint: (t: DocTemplate) => void; embedded?: boolean }) {
  if (loading) return <div className="p-6 text-center text-slate-400 text-sm"><i className="fa-solid fa-spinner fa-spin mr-1.5" />Loading…</div>
  if (!items.length && !embedded) return (
    <div className="p-8 text-center text-slate-400 text-sm">No templates yet. <Link to="/documents" className="text-blue-600 font-medium hover:underline">Add one</Link></div>
  )
  const btn = color === 'emerald' ? 'bg-emerald-600 hover:bg-emerald-700 active:bg-emerald-800' : 'bg-blue-700 hover:bg-blue-800 active:bg-blue-900'
  const rows = items.map((t) => (
    <div key={t.id} className="flex items-center justify-between px-4 py-3.5 hover:bg-slate-50 transition-colors">
      <div className="min-w-0 pr-3">
        <div className="font-semibold text-sm text-slate-800 truncate">{t.name}</div>
        {t.description ? <div className="text-[11px] text-slate-400 mt-0.5 truncate">{t.description}</div> : null}
      </div>
      <button onClick={() => onPrint(t)} className={`px-3.5 py-1.5 ${btn} text-white text-xs font-semibold rounded-md inline-flex items-center gap-1.5 flex-shrink-0 transition-colors`} title="Generate & print this document with the candidate's data filled in">
        <i className="fa-solid fa-print" /> Print
      </button>
    </div>
  ))
  return embedded ? <>{rows}</> : <div className="divide-y divide-slate-100">{rows}</div>
}

// ---------------------------------------------------------------------------
// CHECK LIST TAB (UX #1 - persisted to candidate_document_checklist)
// ---------------------------------------------------------------------------
type ChecklistItem = { key: string; label: string }
const CHECKLIST_ITEMS: ChecklistItem[] = [
  { key: 'passport',                 label: 'Passport' },
  { key: 'cnic',                     label: 'CNIC Front / Back' },
  { key: 'photographs',              label: 'Photographs (white background)' },
  { key: 'educational_certificates', label: 'Educational Certificates' },
  { key: 'experience_certificates',  label: 'Experience Certificates' },
  { key: 'medical_gamca',            label: 'Medical (GAMCA)' },
  { key: 'police_clearance',         label: 'Police Clearance' },
  { key: 'trade_test',               label: 'Trade Test' },
]
type ChecklistState = Record<string, { received: boolean; received_at: string | null; received_by: string; notes: string }>

function ChecklistTab({ candidateId, toast }: { candidateId: number; toast: ReturnType<typeof useToast> }) {
  const [state, setState] = useState<ChecklistState>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api.get<ChecklistState>(`/candidates/${candidateId}/checklist`)
      .then((res) => { if (!cancelled) setState(res.data || {}) })
      .catch((e) => { if (!cancelled) toast.show(apiError(e, 'Failed to load checklist'), 'error') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [candidateId, toast])

  const toggle = async (key: string) => {
    const current = state[key]?.received ?? false
    const next = !current
    setSaving(key)
    // Optimistic UI update
    setState((s) => ({ ...s, [key]: { received: next, received_at: next ? new Date().toISOString() : null, received_by: s[key]?.received_by || '', notes: s[key]?.notes || '' } }))
    try {
      const res = await api.put(`/candidates/${candidateId}/checklist/${key}`, { received: next })
      setState((s) => ({ ...s, [key]: { received: res.data.received, received_at: res.data.received_at, received_by: res.data.received_by, notes: res.data.notes } }))
    } catch (e) {
      // Revert on failure
      setState((s) => ({ ...s, [key]: { ...(s[key] || { received_by: '', notes: '' }), received: current, received_at: current ? (s[key]?.received_at || null) : null } }))
      toast.show(apiError(e, 'Failed to save'), 'error')
    } finally {
      setSaving(null)
    }
  }

  return (
    <div className="p-6">
      <div className="bg-white border border-slate-200 rounded-lg p-5">
        <h4 className="font-semibold text-slate-800 mb-3">Document Check List</h4>
        {loading ? (
          <div className="py-6 text-center text-slate-400"><i className="fa-solid fa-spinner fa-spin text-lg text-blue-600" /></div>
        ) : (
          <div className="space-y-2 text-sm">
            {CHECKLIST_ITEMS.map((item) => {
              const s = state[item.key]
              const checked = !!s?.received
              return (
                <label key={item.key} className="flex items-center gap-2 py-1">
                  <input
                    type="checkbox"
                    className="rounded"
                    checked={checked}
                    disabled={saving === item.key}
                    onChange={() => toggle(item.key)}
                    aria-label={`Mark ${item.label} as received`}
                  />
                  <span className={checked ? 'text-slate-800' : 'text-slate-600'}>{item.label}</span>
                  {saving === item.key && <i className="fa-solid fa-spinner fa-spin text-xs text-blue-500 ml-1" />}
                  {checked && s?.received_by && (
                    <span className="text-xs text-slate-400 ml-auto">
                      {s.received_by}
                      {s.received_at && ` • ${new Date(s.received_at).toLocaleDateString()}`}
                    </span>
                  )}
                </label>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// STATUS TAB
// ---------------------------------------------------------------------------
function StatusTab({ c }: { c: Candidate }) {
  const curStageIdx = WORKFLOW.findIndex((w) => w.value === c.status)
  return (
    <div className="p-6 space-y-4">
      <div className="bg-white border border-slate-200 rounded-lg p-5">
        <h4 className="font-semibold text-slate-800 mb-4">Workflow Stage</h4>
        <div className="space-y-3">
          {WORKFLOW.map((w, i) => {
            const done = curStageIdx >= 0 && i < curStageIdx
            const cur = i === curStageIdx
            return (
              <div key={w.value} className="flex items-center gap-3">
                <div className={`w-8 h-8 rounded-full flex items-center justify-center text-xs ${cur ? 'bg-blue-600 text-white' : done ? 'bg-emerald-500 text-white' : 'bg-slate-100 text-slate-400'}`}>
                  {done ? <i className="fa-solid fa-check" /> : <i className={`fa-solid ${w.icon}`} />}
                </div>
                <div className={`text-sm ${cur ? 'font-semibold text-blue-700' : done ? 'text-emerald-600' : 'text-slate-500'}`}>{w.label}{cur && ' (current)'}</div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// ACTIVITY TAB (UX #6 - wired to /candidates/{id}/audit)
// ---------------------------------------------------------------------------
type AuditEntry = {
  id: number
  entity_type: string
  action: string
  actor_name: string
  summary: string
  occurred_at: string | null
}

function ActivityTab({ c, toast }: { c: Candidate; toast: ReturnType<typeof useToast> }) {
  const [entries, setEntries] = useState<AuditEntry[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api.get<AuditEntry[]>(`/candidates/${c.id}/audit`, { params: { limit: 100 } })
      .then((res) => { if (!cancelled) setEntries(Array.isArray(res.data) ? res.data : []) })
      .catch((e) => { if (!cancelled) toast.show(apiError(e, 'Failed to load activity'), 'error') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [c.id, toast])

  const actionIcon = (action: string): { icon: string; cls: string } => {
    const a = (action || '').toLowerCase()
    if (a.includes('create'))    return { icon: 'fa-plus',        cls: 'bg-emerald-100 text-emerald-600' }
    if (a.includes('delete'))    return { icon: 'fa-trash',       cls: 'bg-rose-100 text-rose-600' }
    if (a.includes('stage'))     return { icon: 'fa-arrow-right', cls: 'bg-amber-100 text-amber-600' }
    if (a.includes('assign'))    return { icon: 'fa-briefcase',   cls: 'bg-indigo-100 text-indigo-600' }
    if (a.includes('wizard'))    return { icon: 'fa-wand-magic-sparkles', cls: 'bg-purple-100 text-purple-600' }
    if (a.includes('reopen'))    return { icon: 'fa-rotate-left', cls: 'bg-orange-100 text-orange-600' }
    if (a.includes('finalize'))  return { icon: 'fa-flag-checkered', cls: 'bg-emerald-100 text-emerald-600' }
    return { icon: 'fa-pen', cls: 'bg-blue-100 text-blue-600' }
  }

  const fmtTime = (iso: string | null): string => {
    if (!iso) return '—'
    try { return new Date(iso).toLocaleString() } catch { return iso }
  }

  return (
    <div className="p-6 space-y-4">
      <div className="bg-white border border-slate-200 rounded-lg p-5">
        <h4 className="font-semibold text-slate-800 mb-4">Record Activity</h4>
        {loading ? (
          <div className="py-6 text-center text-slate-400"><i className="fa-solid fa-spinner fa-spin text-lg text-blue-600" /></div>
        ) : entries.length === 0 ? (
          <div className="py-6 text-center text-sm text-slate-400">
            <div className="mb-2"><i className="fa-solid fa-clock-rotate-left text-2xl text-slate-300" /></div>
            No activity recorded yet
          </div>
        ) : (
          <ol className="space-y-3 text-sm relative">
            {entries.map((e) => {
              const meta = actionIcon(e.action)
              return (
                <li key={e.id} className="flex items-start gap-3">
                  <div className={`w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 ${meta.cls}`}>
                    <i className={`fa-solid ${meta.icon} text-xs`} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-slate-700 font-medium">{e.summary || humanize(e.action)}</div>
                    <div className="text-xs text-slate-400">
                      {e.actor_name || 'System'} • {fmtTime(e.occurred_at)}
                    </div>
                  </div>
                </li>
              )
            })}
          </ol>
        )}
      </div>
    </div>
  )
}
