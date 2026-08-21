// Audit Log viewer with filters + search.
//
// WHY THIS PAGE EXISTS (2026-08-18)
// =================================
// The operator asked: "Add filters and search to the Audit Log page so I can
// quickly find denied or successful actions by office, user, document type,
// and date range."
//
// There was no Audit Log page to add filters to -- the previous one was deleted
// on 2026-07-30 as unusable (see navConfig.ts and section 4.11 of
// PROJECT_DOCUMENTATION.md). And the backend could not have answered the
// question anyway: permission DENIALS were never recorded, so the trail held
// only successes. Both halves are fixed now (alembic 9b5e7d3f2c81 +
// a1c6e8b4d072, app/services/audit.py::log_permission_denied), and this is the
// page that surfaces them.
//
// TWO DELIBERATE DECISIONS, both about not lying to the operator:
//
// 1. OFFICE IS A LABEL, NOT A CHOOSER. Each recruitment office has its own
//    database, so this trail contains exactly ONE office's rows and no query
//    can widen it. Rendering an office dropdown would imply otherwise, and an
//    operator who "filtered to Karachi" would be reading Islamabad's denials
//    under a Karachi heading. The office is therefore displayed, sourced from
//    the server's own answer (`office` in the response) rather than from
//    TENANT_INFO, so the label always describes the data actually returned.
//
// 2. "DENIED" IS ALWAYS OFFERED, even when the trail holds none. A data-driven
//    dropdown would hide the option on exactly the healthy portal where the
//    operator wants to confirm there are no denials -- and its absence reads as
//    "this portal cannot show denials", which was the original bug.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, apiError } from '@/lib/api'
import { useToast } from '@/components/Toast'

type AuditRow = {
  id: number
  entity_type: string
  entity_id: number | null
  action: string
  actor_user_id: number | null
  actor_name: string
  summary: string
  ip_address: string
  user_agent: string
  outcome: string
  permission: string | null
  doc_category: string | null
  occurred_at: string | null
}

type OfficeInfo = { slug: string; name: string; is_default: boolean }

type FilterMeta = {
  entity_types: string[]
  actions: string[]
  actors: { id: number; name: string }[]
  outcomes: string[]
  outcome_counts: { denied: number; success: number }
  permissions: string[]
  doc_categories: string[]
  office: OfficeInfo
}

const PAGE_SIZE = 100

const BLANK: FilterMeta = {
  entity_types: [], actions: [], actors: [],
  outcomes: ['success', 'denied'],
  outcome_counts: { denied: 0, success: 0 },
  permissions: [], doc_categories: [],
  office: { slug: '', name: '', is_default: true },
}

export default function AuditLog() {
  const toast = useToast()
  const [rows, setRows] = useState<AuditRow[]>([])
  const [total, setTotal] = useState(0)
  const [office, setOffice] = useState<OfficeInfo | null>(null)
  const [meta, setMeta] = useState<FilterMeta>(BLANK)
  const [loading, setLoading] = useState(true)

  // Filter state. `outcome` leads because finding denials is the whole point.
  const [outcome, setOutcome] = useState('all')
  const [actorId, setActorId] = useState('all')
  const [docCategory, setDocCategory] = useState('all')
  const [entityType, setEntityType] = useState('all')
  const [action, setAction] = useState('all')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [q, setQ] = useState('')
  const [page, setPage] = useState(0)

  // The typed query is debounced, but ONLY the query. Changing a dropdown
  // fetches immediately -- waiting 300ms after a deliberate click feels broken.
  const [debouncedQ, setDebouncedQ] = useState('')
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q.trim()), 300)
    return () => clearTimeout(t)
  }, [q])

  // Guards against out-of-order responses. Without it, a slow request for an
  // EARLIER filter can land after a fast one and repaint the grid with rows
  // that do not match the controls on screen -- the operator would read
  // successes under a "denied" filter and conclude the portal is broken.
  const reqSeq = useRef(0)

  const params = useMemo(() => {
    const p: Record<string, string | number> = { skip: page * PAGE_SIZE, limit: PAGE_SIZE }
    if (outcome !== 'all') p.outcome = outcome
    if (actorId !== 'all') p.actor_user_id = actorId
    if (docCategory !== 'all') p.doc_category = docCategory
    if (entityType !== 'all') p.entity_type = entityType
    if (action !== 'all') p.action = action
    if (dateFrom) p.date_from = dateFrom
    if (dateTo) p.date_to = dateTo
    if (debouncedQ) p.q = debouncedQ
    return p
  }, [outcome, actorId, docCategory, entityType, action, dateFrom, dateTo, debouncedQ, page])

  const load = useCallback(() => {
    const seq = ++reqSeq.current
    setLoading(true)
    api.get('/users/audit-log', { params })
      .then(({ data }) => {
        if (seq !== reqSeq.current) return      // a newer request has since fired
        setRows(data.items || [])
        setTotal(data.total || 0)
        if (data.office) setOffice(data.office)
      })
      .catch((e) => { if (seq === reqSeq.current) toast.show(apiError(e), 'error') })
      .finally(() => { if (seq === reqSeq.current) setLoading(false) })
  }, [params, toast])

  useEffect(() => { load() }, [load])

  // Filter metadata loads INDEPENDENTLY of the listing. Chaining them would
  // mean a failed metadata call leaves the grid stuck on a spinner -- the exact
  // defect already fixed once on the Roles page (see MiscPages.tsx).
  useEffect(() => {
    api.get('/users/audit-log/_meta/filters')
      .then(({ data }) => setMeta({ ...BLANK, ...data }))
      .catch(() => { /* dropdowns fall back to the always-available choices */ })
  }, [])

  // Any filter change resets pagination. Staying on page 4 of a narrower
  // result set shows an empty grid, which reads as "no matches".
  useEffect(() => { setPage(0) }, [outcome, actorId, docCategory, entityType, action, dateFrom, dateTo, debouncedQ])

  const activeCount = [
    outcome !== 'all', actorId !== 'all', docCategory !== 'all',
    entityType !== 'all', action !== 'all', !!dateFrom, !!dateTo, !!debouncedQ,
  ].filter(Boolean).length

  const clearAll = () => {
    setOutcome('all'); setActorId('all'); setDocCategory('all')
    setEntityType('all'); setAction('all')
    setDateFrom(''); setDateTo(''); setQ('')
  }

  const officeLabel = office?.name || meta.office?.name || ''
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 id="audit-log-heading" className="text-2xl font-bold text-slate-900 flex items-center gap-2.5">
            <i className="fa-solid fa-clipboard-list text-blue-600" /> Audit Log
          </h1>
          {/* The office is stated, not selectable -- see decision 1 above. */}
          <p className="text-[13px] text-slate-500 mt-1">
            Every recorded action for{' '}
            <span className="font-semibold text-slate-700">{officeLabel || 'this office'}</span>
            {office?.slug ? <span className="text-slate-400"> ({office.slug})</span> : null}
            . Each office keeps its own trail in its own database.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="badge badge-rejected" title="Refused attempts recorded in this trail">
            <i className="fa-solid fa-ban mr-1.5" />
            {meta.outcome_counts?.denied ?? 0} denied
          </span>
          <span className="badge badge-active" title="Completed actions recorded in this trail">
            <i className="fa-solid fa-check mr-1.5" />
            {meta.outcome_counts?.success ?? 0} successful
          </span>
        </div>
      </header>

      {/* ---- Filters ---- */}
      <section aria-labelledby="audit-filters-heading" className="card p-4">
        <div className="flex items-center justify-between mb-3">
          <h2 id="audit-filters-heading" className="text-sm font-semibold text-slate-700 flex items-center gap-2">
            <i className="fa-solid fa-filter text-slate-400" /> Filters
            {activeCount > 0 && (
              <span className="badge badge-processing">{activeCount} active</span>
            )}
          </h2>
          <button
            type="button" id="audit-clear-filters" className="btn btn-secondary btn-sm"
            onClick={clearAll} disabled={activeCount === 0}
          >
            <i className="fa-solid fa-rotate-left mr-1.5" /> Clear
          </button>
        </div>

        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <label className="form-label" htmlFor="audit-f-outcome">Outcome</label>
            <select
              id="audit-f-outcome" className="form-select w-full"
              value={outcome} onChange={(e) => setOutcome(e.target.value)}
            >
              <option value="all">All outcomes</option>
              {/* Always both options, even at zero -- see decision 2 above. */}
              {meta.outcomes.map((o) => (
                <option key={o} value={o}>{o === 'denied' ? 'Denied only' : 'Successful only'}</option>
              ))}
            </select>
          </div>

          <div>
            <label className="form-label" htmlFor="audit-f-user">User</label>
            <select
              id="audit-f-user" className="form-select w-full"
              value={actorId} onChange={(e) => setActorId(e.target.value)}
            >
              <option value="all">Everyone</option>
              {meta.actors.map((a) => (
                <option key={a.id} value={String(a.id)}>{a.name || `User #${a.id}`}</option>
              ))}
            </select>
          </div>

          <div>
            <label className="form-label" htmlFor="audit-f-doctype">Document type</label>
            <select
              id="audit-f-doctype" className="form-select w-full"
              value={docCategory} onChange={(e) => setDocCategory(e.target.value)}
              disabled={meta.doc_categories.length === 0}
            >
              <option value="all">
                {meta.doc_categories.length === 0 ? 'No document actions yet' : 'All document types'}
              </option>
              {meta.doc_categories.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>

          <div>
            <label className="form-label" htmlFor="audit-f-entity">Record type</label>
            <select
              id="audit-f-entity" className="form-select w-full"
              value={entityType} onChange={(e) => setEntityType(e.target.value)}
            >
              <option value="all">All records</option>
              {meta.entity_types.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>

          <div>
            <label className="form-label" htmlFor="audit-f-action">Action</label>
            <select
              id="audit-f-action" className="form-select w-full"
              value={action} onChange={(e) => setAction(e.target.value)}
            >
              <option value="all">All actions</option>
              {meta.actions.map((a) => <option key={a} value={a}>{a}</option>)}
            </select>
          </div>

          <div>
            <label className="form-label" htmlFor="audit-f-from">From date</label>
            <input
              id="audit-f-from" type="date" className="form-input w-full"
              value={dateFrom} max={dateTo || undefined}
              onChange={(e) => setDateFrom(e.target.value)}
            />
          </div>

          <div>
            <label className="form-label" htmlFor="audit-f-to">To date</label>
            <input
              id="audit-f-to" type="date" className="form-input w-full"
              value={dateTo} min={dateFrom || undefined}
              onChange={(e) => setDateTo(e.target.value)}
            />
          </div>

          <div>
            <label className="form-label" htmlFor="audit-search">Search</label>
            <div className="relative">
              <i className="fa-solid fa-magnifying-glass absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 text-[13px]" />
              <input
                id="audit-search" type="search" className="form-input w-full pl-9"
                placeholder="user, permission, summary…"
                value={q} onChange={(e) => setQ(e.target.value)}
              />
            </div>
          </div>
        </div>
      </section>

      {/* ---- Results ---- */}
      {/* id="audit-results" and data-audit-row below are the anchors
          scripts/verify_audit_log_ui.py counts. They are load-bearing for
          verification, not decoration: the loading spinner and the "no
          entries" message are BOTH rendered as <tr>, so a harness counting
          `tbody tr` would score an empty result as 1 row and read a
          correctly-filtered-to-nothing page as "the filter did nothing".
          Marking only real data rows makes the count unambiguous. */}
      <section
        id="audit-results"
        aria-labelledby="audit-results-heading"
        className="card overflow-hidden"
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-200">
          <h2 id="audit-results-heading" className="text-sm font-semibold text-slate-700">
            {loading ? 'Loading…' : `${total.toLocaleString()} ${total === 1 ? 'entry' : 'entries'}`}
          </h2>
          {pages > 1 && (
            <div className="flex items-center gap-2">
              <button
                type="button" className="btn btn-secondary btn-xs"
                onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0}
              ><i className="fa-solid fa-chevron-left" /></button>
              <span className="text-[13px] text-slate-500">Page {page + 1} of {pages}</span>
              <button
                type="button" className="btn btn-secondary btn-xs"
                onClick={() => setPage((p) => Math.min(pages - 1, p + 1))}
                disabled={page >= pages - 1}
              ><i className="fa-solid fa-chevron-right" /></button>
            </div>
          )}
        </div>

        <div className="table-scroll">
          <table className="data-table responsive-cards w-full">
            <thead>
              <tr>
                <th>Outcome</th><th>When</th><th>User</th>
                <th>Action</th><th>Record</th><th>Document type</th>
                <th>Details</th><th>IP</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={8} className="text-center py-12 text-slate-400">
                  <i className="fa-solid fa-spinner fa-spin text-xl text-blue-600" />
                </td></tr>
              ) : rows.length === 0 ? (
                <tr><td colSpan={8} className="text-center py-12 text-slate-400">
                  {activeCount > 0
                    // Distinguishing "nothing matched your filters" from "the
                    // trail is empty" matters: the first is a search result,
                    // the second could be a broken audit pipeline.
                    ? <>No entries match these filters.{' '}
                        <button type="button" className="text-blue-600 underline" onClick={clearAll}>
                          Clear filters
                        </button></>
                    : 'Nothing has been recorded yet.'}
                </td></tr>
              ) : rows.map((r) => {
                const denied = (r.outcome || 'success') === 'denied'
                return (
                  <tr
                    key={r.id}
                    data-audit-row={r.id}
                    data-audit-outcome={denied ? 'denied' : 'success'}
                    className={denied ? 'bg-rose-50/40' : undefined}
                  >
                    <td data-label="Outcome">
                      <span
                        className={`badge ${denied ? 'badge-rejected' : 'badge-active'}`}
                        title={denied && r.permission ? `Refused: ${r.permission}` : undefined}
                      >
                        <i className={`fa-solid ${denied ? 'fa-ban' : 'fa-check'} mr-1.5`} />
                        {denied ? 'Denied' : 'Success'}
                      </span>
                    </td>
                    <td data-label="When" className="text-slate-500 text-[13px] whitespace-nowrap">
                      {r.occurred_at ? new Date(r.occurred_at).toLocaleString() : '—'}
                    </td>
                    <td data-label="User">{r.actor_name || '—'}</td>
                    <td data-label="Action">
                      <span className="font-medium text-slate-700">{r.action || '—'}</span>
                      {r.permission && (
                        <div className="text-[12px] font-mono text-rose-700 mt-0.5">{r.permission}</div>
                      )}
                    </td>
                    <td data-label="Record" className="text-[13px]">
                      {r.entity_type || '—'}{r.entity_id ? <span className="text-slate-400"> #{r.entity_id}</span> : null}
                    </td>
                    <td data-label="Document type" className="text-[13px]">{r.doc_category || '—'}</td>
                    <td data-label="Details" className="text-[13px] text-slate-600 max-w-[30rem]">{r.summary || '—'}</td>
                    <td data-label="IP" className="font-mono text-[12px] text-slate-500">{r.ip_address || '—'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
