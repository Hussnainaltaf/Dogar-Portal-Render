import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, apiError, apiValidation } from '@/lib/api'
import { getRows, invalidateOptions } from '@/lib/options'
import { useToast } from '@/components/Toast'
import { Modal } from '@/components/ui'
import { humanize } from '@/lib/format'
import { useLiveStatus } from '@/lib/useLiveStatus'

interface Demand {
  id: number; file_number?: string; file_number_display?: string; client_name?: string
  client_id?: number; visa_number?: string; receiving_date?: string
  sponsor_name?: string; country?: string; embassy?: string; status?: string
  trades_count?: number; total_slots?: number; assigned_count?: number; [k: string]: unknown
}

function fmtDate(v?: string): string {
  if (!v) return '—'
  const d = new Date(v)
  if (isNaN(d.getTime())) return String(v)
  return d.toLocaleDateString('en-GB')
}

interface ClientOpt { id: number; company_name: string }
interface EmbassyOpt { id: number; name: string }
interface VisaCatOpt { id: number; name: string }

const STATUS_PILL: Record<string, string> = { open: 'badge-processing', active: 'badge-active', filled: 'badge-filled', closed: 'badge-inactive', cancelled: 'badge-cancelled' }

// ───────────────────────── Searchable select (combobox) ─────────────────────────
function SearchSelect({ value, valueLabel, options, onSelect, placeholder, disabled, extraAction }: {
  value: string; valueLabel: string
  options: { id: string | number; label: string }[]
  onSelect: (id: string, label: string) => void
  placeholder: string; disabled?: boolean
  extraAction?: React.ReactNode
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const wrapRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    function onDoc(e: MouseEvent) { if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])
  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase()
    const list = s ? options.filter((o) => o.label.toLowerCase().includes(s)) : options
    return list.slice(0, 60)
  }, [q, options])
  return (
    <div className="flex gap-2">
      <div ref={wrapRef} className="relative flex-1">
        <button type="button" disabled={disabled} onClick={() => { setOpen((v) => !v); setQ('') }}
          className="form-input w-full text-left flex items-center justify-between gap-2 disabled:opacity-60">
          <span className={value ? 'text-slate-800' : 'text-slate-400'}>{value ? valueLabel : placeholder}</span>
          <i className="fa-solid fa-chevron-down text-[10px] text-slate-400" />
        </button>
        {open && (
          <div className="absolute z-30 mt-1 w-full bg-white border border-slate-200 rounded-lg shadow-xl overflow-hidden">
            <div className="p-2 border-b border-slate-100">
              <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Type to search…"
                className="w-full text-sm px-2.5 py-1.5 border border-slate-200 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500/30" />
            </div>
            <div className="max-h-52 overflow-y-auto">
              {filtered.length === 0
                ? <div className="px-3 py-3 text-xs text-slate-400 text-center">No matches</div>
                : filtered.map((o) => (
                  <button key={o.id} type="button"
                    onClick={() => { onSelect(String(o.id), o.label); setOpen(false) }}
                    className={`w-full text-left px-3 py-2 text-sm hover:bg-blue-50 ${String(o.id) === value ? 'bg-blue-50 text-blue-700 font-semibold' : 'text-slate-700'}`}>
                    {o.label}
                  </button>
                ))}
            </div>
          </div>
        )}
      </div>
      {extraAction}
    </div>
  )
}

// ───────────────────────── Create Demand wizard state ─────────────────────────
interface Position { trade: string; quantity: string; salary: string }
interface AttachedDocumentMeta { id: string; name: string; size: number; type: string; lastModified: number }
interface DemandDraft { version: 1; step: number; form: FormT; positions: Position[]; attachments: AttachedDocumentMeta[]; updatedAt: number }
const BLANK_POS: Position = { trade: '', quantity: '1', salary: '' }
const DRAFT_KEY = 'dogar:demand-wizard:draft:v1'

const BLANK_FORM = {
  file_number: '', receiving_date: new Date().toISOString().slice(0, 10), status: 'active',
  client_id: '', client_label: '',
  visa_number: '', bataka_number: '', visa_issue_date: '', visa_issue_date_hijri: '',
  permission_no: '', permission_date: '', country: 'Saudi Arabia', embassy: '',
  reference: '', notes: '',
  sponsor_name: '', sponsor_name_arabic: '', sponsor_phone: '', sponsor_alt_phone: '',
  sponsor_address: '', sponsor_address_arabic: '',
}
type FormT = typeof BLANK_FORM

const BLANK_CLIENT = {
  client_type: 'Individual', status: 'active', company_name: '', email: '', phone: '',
  street: '', city: '', state: '', country: '',
  opening_balance: '0', opening_balance_direction: 'Owed to us',
  opening_balance_date: new Date().toISOString().slice(0, 10), notes: '',
}

export default function Demands() {
  const toast = useToast()
  const navigate = useNavigate()
  const [rows, setRows] = useState<Demand[]>([])
  const [total, setTotal] = useState(0)
  const [q, setQ] = useState('')
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(15)

  // Wizard
  const [addOpen, setAddOpen] = useState(false)
  const [step, setStep] = useState(1)
  const [form, setForm] = useState<FormT>({ ...BLANK_FORM })
  const [positions, setPositions] = useState<Position[]>([{ ...BLANK_POS }])
  const [attachments, setAttachments] = useState<AttachedDocumentMeta[]>([])
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const submitLock = useRef(false)
  const draftReady = useRef(false)
  const applyingRemoteDraft = useRef(false)

  // Lookup data
  const [clients, setClients] = useState<ClientOpt[]>([])
  const [embassies, setEmbassies] = useState<EmbassyOpt[]>([])
  const [cities, setCities] = useState<{ id: number; name: string }[]>([])
  const [visaCats, setVisaCats] = useState<VisaCatOpt[]>([])
  const [lookupsLoading, setLookupsLoading] = useState(false)

  // Inline sub-modals
  const [addClientOpen, setAddClientOpen] = useState(false)
  const [clientForm, setClientForm] = useState({ ...BLANK_CLIENT })
  const [clientSaving, setClientSaving] = useState(false)
  const [addCatOpen, setAddCatOpen] = useState<number | null>(null) // position index
  const [catForm, setCatForm] = useState({ name: '', name_arabic: '', is_active: true })
  const [catSaving, setCatSaving] = useState(false)

  function load() {
    setLoading(true)
    const params: Record<string, string | number> = { limit: pageSize, skip: (page - 1) * pageSize }
    if (q) params.q = q
    api.get('/demands/', { params })
      .then(({ data }) => { setRows(data.items || []); setTotal(data.total || 0) })
      .catch((e) => toast.show(apiError(e), 'error'))
      .finally(() => setLoading(false))
  }
  useEffect(() => {
    const id = window.setTimeout(load, 250)
    return () => window.clearTimeout(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, page, pageSize])
  // Reset to page 1 when the search query changes.
  useEffect(() => { setPage(1) }, [q])

  // Live status: refetch the demand list when any demand-file status changes on
  // the server (SSE with automatic polling fallback). `load` is recreated each
  // render, so keep the latest in a ref and call it from a stable callback.
  const loadRef = useRef(load)
  loadRef.current = load
  useLiveStatus(useCallback(() => { loadRef.current() }, []))

  function clearFieldError(path: string) {
    setFieldErrors((current) => {
      if (!current[path]) return current
      const next = { ...current }; delete next[path]; return next
    })
  }
  function setF<K extends keyof FormT>(k: K, v: string) {
    clearFieldError(String(k))
    setForm((f) => ({ ...f, [k]: v }))
  }
  const ErrorText = ({ path }: { path: string }) => fieldErrors[path]
    ? <p id={`demand-error-${path.replace(/\./g, '-')}`} role="alert" data-field-error={path} className="mt-1 text-xs font-semibold text-rose-600">{fieldErrors[path]}</p>
    : null
  const inputA11y = (path: string) => ({
    id: `demand-${path.replace(/\./g, '-')}`,
    'aria-invalid': fieldErrors[path] ? true : undefined,
    'aria-describedby': fieldErrors[path] ? `demand-error-${path.replace(/\./g, '-')}` : undefined,
    'data-demand-field': path,
  })

  function readDraft(): DemandDraft | null {
    try {
      const parsed = JSON.parse(localStorage.getItem(DRAFT_KEY) || 'null')
      return parsed?.version === 1 && parsed.form && Array.isArray(parsed.positions) ? parsed as DemandDraft : null
    } catch { return null }
  }

  useEffect(() => {
    const sync = (event: StorageEvent) => {
      if (event.key !== DRAFT_KEY || !event.newValue) return
      try {
        const draft = JSON.parse(event.newValue) as DemandDraft
        if (draft.version !== 1) return
        // Applying remote state must not immediately write the same draft back.
        // Without this guard, two open tabs can bounce storage events forever.
        applyingRemoteDraft.current = true
        setForm({ ...BLANK_FORM, ...draft.form })
        setPositions(draft.positions.length ? draft.positions : [{ ...BLANK_POS }])
        setAttachments(draft.attachments || [])
        setStep(Math.min(3, Math.max(1, Number(draft.step) || 1)))
      } catch { /* Ignore malformed data written outside the portal. */ }
    }
    window.addEventListener('storage', sync)
    return () => window.removeEventListener('storage', sync)
  }, [])

  useEffect(() => {
    if (!addOpen || !draftReady.current) return
    if (applyingRemoteDraft.current) {
      applyingRemoteDraft.current = false
      return
    }
    const draft: DemandDraft = { version: 1, step, form, positions, attachments, updatedAt: Date.now() }
    localStorage.setItem(DRAFT_KEY, JSON.stringify(draft))
  }, [addOpen, step, form, positions, attachments])

  async function openAdd() {
    const draft = readDraft()
    setForm(draft ? { ...BLANK_FORM, ...draft.form } : { ...BLANK_FORM, receiving_date: new Date().toISOString().slice(0, 10) })
    setPositions(draft?.positions?.length ? draft.positions : [{ ...BLANK_POS }])
    setAttachments(draft?.attachments || [])
    setStep(draft ? Math.min(3, Math.max(1, Number(draft.step) || 1)) : 1)
    setFieldErrors({})
    draftReady.current = true
    setAddOpen(true)
    setLookupsLoading(true)
    try {
      // Reference lists come from the shared cache (lib/options.ts): compact
      // projections, one request per list per session. Re-opening this wizard
      // no longer refetches ~330 KB of client rows it never displays.
      const [nf, cl, em, ci, vc] = await Promise.all([
        api.get('/demands/config/next-file-number'),
        getRows('clients'),
        getRows('embassies'),
        getRows('cities'),
        getRows('visaCategories'),
      ])
      if (!draft) setForm((f) => ({ ...f, file_number: nf.data?.display || '' }))
      setClients(cl.map((c) => ({ id: c.id, company_name: c.name })))
      setEmbassies(em.map((e) => ({ id: e.id, name: e.name })))
      setCities(ci.map((c) => ({ id: c.id, name: c.name })))
      setVisaCats(vc.map((v) => ({ id: v.id, name: v.name })))
    } catch (e) { toast.show(apiError(e), 'error') }
    finally { setLookupsLoading(false) }
  }

  // ── Step validation (mirrors the reference: required fields per step) ──
  const step1Valid = !!(form.file_number.trim() && form.receiving_date && form.status && form.client_id
    && form.visa_number.trim() && form.bataka_number.trim() && form.visa_issue_date
    && form.country.trim() && form.embassy)
  const step2Valid = !!form.sponsor_name.trim()
  const readyPositions = positions.filter((p) => p.trade.trim() && Number(p.quantity) >= 1)
  const totalVisas = readyPositions.reduce((s, p) => s + (Number(p.quantity) || 0), 0)
  const step3Valid = readyPositions.length > 0 && positions.every((p) => !p.trade.trim() || Number(p.quantity) >= 1)

  function focusFirstError(errors: Record<string, string>) {
    const path = Object.keys(errors)[0]
    if (!path) return
    const targetStep = path.startsWith('positions.') ? 3 : path.startsWith('sponsor_') ? 2 : 1
    setStep(targetStep)
    window.setTimeout(() => document.getElementById(`demand-${path.replace(/\./g, '-')}`)?.focus(), 0)
  }

  async function submitWizard() {
    if (submitLock.current) return
    if (!step3Valid) {
      const errors = { 'positions.0.trade': 'Add at least one complete visa position' }
      setFieldErrors(errors); focusFirstError(errors); return
    }
    submitLock.current = true
    setFieldErrors({})
    setSaving(true)
    try {
      const payload: Record<string, unknown> = {
        validation_mode: 'wizard',
        client_id: Number(form.client_id),
        file_number: form.file_number.trim(),
        receiving_date: form.receiving_date, status: form.status,
        visa_number: form.visa_number.trim(), bataka_number: form.bataka_number.trim(),
        visa_issue_date: form.visa_issue_date,
        country: form.country.trim(), embassy: form.embassy,
        sponsor_name: form.sponsor_name.trim(),
        positions: readyPositions.map((p) => ({ trade: p.trade.trim(), quantity: Number(p.quantity) || 1, salary: Number(p.salary) || 0 })),
      }
      // Optional fields only when non-empty
      const opt: (keyof FormT)[] = ['visa_issue_date_hijri', 'permission_no', 'permission_date', 'reference', 'notes',
        'sponsor_name_arabic', 'sponsor_phone', 'sponsor_alt_phone', 'sponsor_address', 'sponsor_address_arabic']
      opt.forEach((k) => { const v = String(form[k] || '').trim(); if (v) payload[k] = v })
      const { data } = await api.post('/demands/', payload)
      toast.show('Demand file created', 'success')
      // Candidates' "Demand" filter must show the new file immediately.
      invalidateOptions('demands')
      localStorage.removeItem(DRAFT_KEY)
      draftReady.current = false
      setAddOpen(false)
      if (data?.id) navigate(`/demands/${data.id}`)
      else load()
    } catch (err) {
      const validation = apiValidation(err)
      if (Object.keys(validation.fieldErrors).length) {
        setFieldErrors(validation.fieldErrors)
        focusFirstError(validation.fieldErrors)
      } else {
        toast.show(validation.formError || apiError(err), 'error')
      }
    } finally {
      submitLock.current = false
      setSaving(false)
    }
  }

  // ── Inline Add Client ──
  async function submitClient(e: React.FormEvent) {
    e.preventDefault()
    if (!clientForm.company_name.trim()) { toast.show('Name is required', 'error'); return }
    setClientSaving(true)
    try {
      const payload: Record<string, unknown> = {}
      Object.entries(clientForm).forEach(([k, v]) => { if (String(v).trim() !== '') payload[k] = v })
      const { data } = await api.post('/clients/', payload)
      toast.show('Client created', 'success')
      // The cached client list is now stale for every other page.
      invalidateOptions('clients')
      setClients((c) => [{ id: data.id, company_name: data.company_name }, ...c])
      setForm((f) => ({ ...f, client_id: String(data.id), client_label: data.company_name }))
      setAddClientOpen(false)
    } catch (err) { toast.show(apiError(err), 'error') }
    finally { setClientSaving(false) }
  }

  // ── Inline Add Visa Category ──
  async function submitCat(e: React.FormEvent) {
    e.preventDefault()
    if (!catForm.name.trim()) { toast.show('Name is required', 'error'); return }
    setCatSaving(true)
    try {
      const { data } = await api.post('/lookups/visa-categories', { name: catForm.name.trim(), name_arabic: catForm.name_arabic.trim(), is_active: catForm.is_active })
      toast.show('Visa category created', 'success')
      invalidateOptions('visaCategories')
      setVisaCats((v) => [{ id: data.id, name: data.name }, ...v])
      if (addCatOpen !== null) setPositions((ps) => ps.map((p, i) => i === addCatOpen ? { ...p, trade: data.name } : p))
      setAddCatOpen(null)
    } catch (err) { toast.show(apiError(err), 'error') }
    finally { setCatSaving(false) }
  }

  const setPos = (i: number, patch: Partial<Position>) => {
    Object.keys(patch).forEach((key) => clearFieldError(`positions.${i}.${key}`))
    setPositions((ps) => ps.map((p, idx) => idx === i ? { ...p, ...patch } : p))
  }

  function addAttachmentFiles(files: FileList | null) {
    if (!files) return
    const added = Array.from(files).map((file) => ({
      id: `${file.name}:${file.size}:${file.lastModified}`,
      name: file.name,
      size: file.size,
      type: file.type || 'application/octet-stream',
      lastModified: file.lastModified,
    }))
    setAttachments((current) => [...current.filter((x) => !added.some((a) => a.id === x.id)), ...added])
  }

  const clientOpts = useMemo(() => clients.map((c) => ({ id: c.id, label: c.company_name })), [clients])
  const catOpts = useMemo(() => visaCats.map((v) => ({ id: v.name, label: v.name })), [visaCats])
  const cityOpts = useMemo(() => cities.map((c) => ({ id: c.name, label: c.name })), [cities])

  const StepTab = ({ n, label }: { n: number; label: string }) => {
    const done = n < step
    const active = n === step
    return (
      <div className={`flex-1 flex items-center gap-2 px-3 sm:px-4 py-2.5 text-[13px] font-semibold border-b-2 ${active ? 'border-blue-600 text-blue-700 bg-blue-50/60' : done ? 'border-emerald-500 text-emerald-700' : 'border-transparent text-slate-400'}`}>
        <span className={`inline-flex items-center justify-center w-5 h-5 rounded-full text-[11px] ${active ? 'bg-blue-600 text-white' : done ? 'bg-emerald-500 text-white' : 'bg-slate-200 text-slate-500'}`}>
          {done ? <i className="fa-solid fa-check text-[9px]" /> : n}
        </span>
        <span className="hidden sm:inline">{label}</span>
        <span className="sm:hidden">{label.split(' ')[0]}</span>
      </div>
    )
  }

  const Req = () => <span aria-hidden="true" className="text-rose-500"> *</span>

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 flex items-center gap-2.5"><i className="fa-regular fa-folder-open text-blue-600" /> Demand Files</h1>
          <p className="text-sm text-slate-500 mt-1">{total.toLocaleString()} demand files</p>
        </div>
        <button onClick={openAdd} className="inline-flex items-center gap-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg shadow-sm transition">
          <i className="fa-solid fa-plus" /> Add Demand File
        </button>
      </div>

      <div className="card"><div className="card-body">
        <div className="relative max-w-md">
          <i className="fa-solid fa-magnifying-glass absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 text-sm" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="File no, permission, sponsor…" className="form-input w-full pl-9" />
        </div>
      </div></div>

      <div className="card overflow-hidden"><div className="overflow-x-auto table-scroll">
        <table className="data-table responsive-cards w-full">
          <thead><tr><th>File #</th><th>Client</th><th>Visa #</th><th>Receiving date</th><th>Sponsor</th><th>Embassy</th><th>Status</th><th>Trades</th><th className="text-right">Actions</th></tr></thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={9} className="text-center py-12 text-slate-400"><i className="fa-solid fa-spinner fa-spin text-xl text-blue-600" /></td></tr>
            ) : rows.length === 0 ? (
              <tr><td colSpan={9} className="text-center py-12 text-slate-400"><i className="fa-regular fa-folder-open text-3xl block mb-2 opacity-50" />No demand files found</td></tr>
            ) : rows.map((d) => (
              <tr key={d.id}>
                <td data-label="File #" data-primary>
                  <Link to={`/demands/${d.id}`} className="font-mono text-[13px] font-semibold text-blue-600 hover:text-blue-700 hover:underline">{d.file_number_display || d.file_number || '—'}</Link>
                </td>
                <td data-label="Client">
                  {d.client_name
                    ? (d.client_id
                        ? <Link to={`/clients/${d.client_id}`} className="text-blue-600 hover:text-blue-700 hover:underline font-semibold">{d.client_name}</Link>
                        : <span className="text-slate-700">{d.client_name}</span>)
                    : <span className="text-slate-400">—</span>}
                </td>
                <td data-label="Visa #" className="font-mono text-[12px]">{d.visa_number || '—'}</td>
                <td data-label="Receiving date">{fmtDate(d.receiving_date)}</td>
                <td data-label="Sponsor">{d.sponsor_name || '—'}</td>
                <td data-label="Embassy">{d.embassy || d.country || '—'}</td>
                <td data-label="Status"><span className={`badge ${STATUS_PILL[d.status || ''] || 'badge-pending'}`}>{humanize(d.status) || '—'}</span></td>
                <td data-label="Trades">{d.assigned_count ?? 0}/{d.total_slots ?? 0}</td>
                <td data-label="Actions" className="text-right"><Link to={`/demands/${d.id}`} className="inline-flex items-center justify-center w-9 h-9 rounded-lg text-blue-600 hover:text-blue-700 hover:bg-blue-50 transition" title="View"><i className="fa-regular fa-eye" /></Link></td>
              </tr>
            ))}
          </tbody>
        </table>
        {/* Pagination */}
        <div className="px-4 py-3 border-t border-slate-100 flex items-center justify-between text-xs text-slate-500">
          <div>{total.toLocaleString()} total · Page {page} of {Math.max(1, Math.ceil(total / pageSize))}</div>
          <div className="flex items-center gap-2">
            <select value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1) }}
              className="border border-slate-200 rounded px-2 py-1 text-xs">
              {[10, 15].map((n) => <option key={n} value={n}>{n} per page</option>)}
            </select>
            <button disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}
              className="px-3 py-1 border border-slate-200 rounded hover:bg-slate-50 disabled:opacity-40">Prev</button>
            <button disabled={page >= Math.ceil(total / pageSize)} onClick={() => setPage((p) => p + 1)}
              className="px-3 py-1 border border-slate-200 rounded hover:bg-slate-50 disabled:opacity-40">Next</button>
          </div>
        </div>
      </div></div>

      {/* ══════════ CREATE DEMAND FILE — 3-STEP WIZARD ══════════ */}
      <Modal open={addOpen} onClose={() => setAddOpen(false)} maxWidth="max-w-3xl"
        title={<span className="flex items-center gap-2"><i className="fa-regular fa-folder-open text-blue-600" /> Create Demand File</span>}>
        {/* Step tabs */}
        <div className="flex border-b border-slate-100">
          <StepTab n={1} label="Demand file details" />
          <StepTab n={2} label="Sponsor information" />
          <StepTab n={3} label="Visa positions" />
        </div>

        <div className="p-5">
          {/* ───── STEP 1: Demand file details ───── */}
          {step === 1 && (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <div>
                <label htmlFor="demand-file_number" className="form-label">File number<Req /></label>
                <input {...inputA11y('file_number')} value={form.file_number} onChange={(e) => setF('file_number', e.target.value)}
                  className="form-input w-full font-mono" placeholder={lookupsLoading ? 'Generating…' : ''} required />
                <ErrorText path="file_number" />
              </div>
              <div>
                <label htmlFor="demand-receiving_date" className="form-label">Receiving date<Req /></label>
                <input {...inputA11y('receiving_date')} type="date" value={form.receiving_date} onChange={(e) => setF('receiving_date', e.target.value)} className="form-input w-full" required />
                <ErrorText path="receiving_date" />
              </div>
              <div>
                <label htmlFor="demand-status" className="form-label">Status<Req /></label>
                <select {...inputA11y('status')} value={form.status} onChange={(e) => setF('status', e.target.value)} className="form-input w-full">
                  <option value="active">Active</option>
                  <option value="processing">Processing</option>
                  <option value="filled">Filled</option>
                  <option value="cancelled">Cancelled</option>
                </select>
                <ErrorText path="status" />
              </div>
              <div className="sm:col-span-3" id="demand-client_id" tabIndex={-1}>
                <label className="form-label">Client<Req /></label>
                <SearchSelect value={form.client_id} valueLabel={form.client_label}
                  options={clientOpts} placeholder={lookupsLoading ? 'Loading clients…' : 'Search client…'}
                  onSelect={(id, label) => setForm((f) => ({ ...f, client_id: id, client_label: label }))}
                  disabled={lookupsLoading}
                  extraAction={
                    <button type="button" onClick={() => { setClientForm({ ...BLANK_CLIENT }); setAddClientOpen(true) }}
                      className="shrink-0 inline-flex items-center gap-1.5 border border-blue-200 text-blue-700 hover:bg-blue-50 text-sm font-semibold px-3 py-2 rounded-lg">
                      <i className="fa-solid fa-plus" /> Add client
                    </button>
                  } />
                <ErrorText path="client_id" />
              </div>
              <div>
                <label htmlFor="demand-visa_number" className="form-label">Visa number<Req /></label>
                <input {...inputA11y('visa_number')} value={form.visa_number} onChange={(e) => setF('visa_number', e.target.value)} className="form-input w-full" required />
                <ErrorText path="visa_number" />
              </div>
              <div>
                <label htmlFor="demand-bataka_number" className="form-label">Bataka number<Req /></label>
                <input {...inputA11y('bataka_number')} value={form.bataka_number} onChange={(e) => setF('bataka_number', e.target.value)} className="form-input w-full" required />
                <ErrorText path="bataka_number" />
              </div>
              <div>
                <label htmlFor="demand-visa_issue_date" className="form-label">Visa issue date<Req /></label>
                <input {...inputA11y('visa_issue_date')} type="date" value={form.visa_issue_date} onChange={(e) => setF('visa_issue_date', e.target.value)} className="form-input w-full" required />
                <ErrorText path="visa_issue_date" />
              </div>
              <div>
                <label htmlFor="demand-visa_issue_date_hijri" className="form-label">Visa issue date (Hijri)</label>
                <input {...inputA11y('visa_issue_date_hijri')} value={form.visa_issue_date_hijri} onChange={(e) => setF('visa_issue_date_hijri', e.target.value)} className="form-input w-full" placeholder="e.g. 15-06-1445" />
              </div>
              <div>
                <label htmlFor="demand-permission_no" className="form-label">Permission number</label>
                <input {...inputA11y('permission_no')} value={form.permission_no} onChange={(e) => setF('permission_no', e.target.value)} className="form-input w-full" />
              </div>
              <div>
                <label htmlFor="demand-permission_date" className="form-label">Permission date</label>
                <input {...inputA11y('permission_date')} type="date" value={form.permission_date} onChange={(e) => setF('permission_date', e.target.value)} className="form-input w-full" />
              </div>
              <div>
                <label htmlFor="demand-country" className="form-label">Country<Req /></label>
                <select {...inputA11y('country')} value={form.country} onChange={(e) => setF('country', e.target.value)} className="form-input w-full">
                  <option value="">—</option>
                  <option value="Saudi Arabia">Saudi Arabia</option>
                </select>
                <ErrorText path="country" />
              </div>
              <div className="sm:col-span-2">
                <label htmlFor="demand-embassy" className="form-label">Embassy<Req /></label>
                <select {...inputA11y('embassy')} value={form.embassy} onChange={(e) => setF('embassy', e.target.value)} className="form-input w-full">
                  <option value="">—</option>
                  {embassies.map((e2) => <option key={e2.id} value={e2.name}>{e2.name}</option>)}
                </select>
                <ErrorText path="embassy" />
              </div>
              <div className="sm:col-span-3">
                <label htmlFor="demand-reference" className="form-label">Reference</label>
                <input {...inputA11y('reference')} value={form.reference} onChange={(e) => setF('reference', e.target.value)} className="form-input w-full" />
              </div>
              <div className="sm:col-span-3">
                <label htmlFor="demand-notes" className="form-label">Notes</label>
                <textarea {...inputA11y('notes')} value={form.notes} onChange={(e) => setF('notes', e.target.value)} className="form-input w-full" rows={2} />
              </div>
            </div>
          )}

          {/* ───── STEP 2: Sponsor information ───── */}
          {step === 2 && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label htmlFor="demand-sponsor_name" className="form-label">Sponsor name<Req /></label>
                <input {...inputA11y('sponsor_name')} value={form.sponsor_name} onChange={(e) => setF('sponsor_name', e.target.value)} className="form-input w-full" required />
                <ErrorText path="sponsor_name" />
              </div>
              <div>
                <label htmlFor="demand-sponsor_name_arabic" className="form-label">Sponsor name (Arabic)</label>
                <input {...inputA11y('sponsor_name_arabic')} dir="rtl" value={form.sponsor_name_arabic} onChange={(e) => setF('sponsor_name_arabic', e.target.value)} className="form-input w-full" />
              </div>
              <div>
                <label htmlFor="demand-sponsor_phone" className="form-label">Sponsor phone</label>
                <input {...inputA11y('sponsor_phone')} value={form.sponsor_phone} onChange={(e) => setF('sponsor_phone', e.target.value)} className="form-input w-full" />
              </div>
              <div>
                <label htmlFor="demand-sponsor_alt_phone" className="form-label">Sponsor alternative phone</label>
                <input {...inputA11y('sponsor_alt_phone')} value={form.sponsor_alt_phone} onChange={(e) => setF('sponsor_alt_phone', e.target.value)} className="form-input w-full" />
              </div>
              <div className="sm:col-span-2">
                <label className="form-label">Sponsor address</label>
                <SearchSelect value={form.sponsor_address} valueLabel={form.sponsor_address}
                  options={cityOpts} placeholder="Search or select city…"
                  onSelect={(_, label) => setF('sponsor_address', label)} />
              </div>
              <div className="sm:col-span-2">
                <label htmlFor="demand-sponsor_address_arabic" className="form-label">Sponsor address (Arabic)</label>
                <input {...inputA11y('sponsor_address_arabic')} dir="rtl" value={form.sponsor_address_arabic} onChange={(e) => setF('sponsor_address_arabic', e.target.value)} className="form-input w-full" />
              </div>
              <section className="sm:col-span-2 rounded-xl border border-blue-100 bg-blue-50/40 p-3" aria-label="Attached document metadata">
                <div className="flex items-center justify-between gap-3">
                  <div><div className="text-sm font-bold text-slate-800">Attached documents</div><p className="text-xs text-slate-500">File metadata is saved in the draft and synchronized across tabs.</p></div>
                  <label className="cursor-pointer rounded-lg border border-blue-200 bg-white px-3 py-2 text-xs font-semibold text-blue-700 hover:bg-blue-50">
                    <i className="fa-solid fa-paperclip mr-1" /> Attach
                    <input data-testid="demand-attachments" type="file" multiple className="hidden" onChange={(e) => { addAttachmentFiles(e.target.files); e.target.value = '' }} />
                  </label>
                </div>
                {attachments.length > 0 && <ul className="mt-2 space-y-1" data-testid="attachment-metadata-list">
                  {attachments.map((file) => <li key={file.id} className="flex items-center justify-between gap-2 rounded-md bg-white px-2.5 py-1.5 text-xs text-slate-700">
                    <span className="truncate"><i className="fa-regular fa-file mr-1.5 text-blue-500" />{file.name} <span className="text-slate-400">({Math.max(1, Math.round(file.size / 1024))} KB)</span></span>
                    <button type="button" aria-label={`Remove ${file.name}`} onClick={() => setAttachments((items) => items.filter((x) => x.id !== file.id))} className="text-rose-500 hover:text-rose-700"><i className="fa-solid fa-xmark" /></button>
                  </li>)}
                </ul>}
              </section>
            </div>
          )}

          {/* ───── STEP 3: Visa positions ───── */}
          {step === 3 && (
            <div className="space-y-4">
              <div className="bg-blue-50 border border-blue-100 rounded-lg p-3.5 flex items-start justify-between gap-3">
                <div className="flex items-start gap-2.5">
                  <span className="inline-flex items-center justify-center w-8 h-8 rounded-lg bg-blue-600 text-white"><i className="fa-solid fa-briefcase text-sm" /></span>
                  <div>
                    <div className="text-sm font-bold text-slate-800">Visa positions</div>
                    <p className="text-xs text-slate-500 mt-0.5">Add each job type you need under this demand file. Choose a visa category, how many workers you need, and an optional salary.</p>
                  </div>
                </div>
                <div className="shrink-0 text-right text-[11px] font-semibold whitespace-nowrap">
                  <span className="text-emerald-600">{readyPositions.length} position{readyPositions.length === 1 ? '' : 's'} ready</span>
                  <span className="text-slate-300 mx-1.5">·</span>
                  <span className="text-blue-600">{totalVisas} visa{totalVisas === 1 ? '' : 's'} total</span>
                </div>
              </div>

              {positions.map((p, i) => {
                const ready = p.trade.trim() && Number(p.quantity) >= 1
                return (
                  <div key={i} className="border border-slate-200 rounded-xl overflow-hidden">
                    <div className="flex items-center justify-between px-4 py-2.5 bg-slate-50/70 border-b border-slate-100">
                      <div className="flex items-center gap-2 text-[13px]">
                        <span className={`inline-flex items-center justify-center w-5 h-5 rounded-full text-[11px] font-bold ${ready ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-200 text-slate-500'}`}>{i + 1}</span>
                        {ready
                          ? <><span className="font-bold text-slate-800">{p.trade}</span><span className="text-[11px] text-slate-400 font-semibold">Qty {p.quantity}</span></>
                          : <><span className="font-semibold text-slate-600">Position {i + 1}</span><span className="text-[10px] bg-amber-50 text-amber-700 border border-amber-200 px-1.5 py-0.5 rounded font-semibold">Required fields missing</span></>}
                      </div>
                      {positions.length > 1 && (
                        <button type="button" onClick={() => setPositions((ps) => ps.filter((_, idx) => idx !== i))}
                          className="text-rose-500 hover:text-rose-600 text-xs font-semibold"><i className="fa-regular fa-trash-can mr-1" />Remove</button>
                      )}
                    </div>
                    <div className="p-4 grid grid-cols-1 sm:grid-cols-[1fr_auto_auto] gap-3 items-end">
                      <div id={`demand-positions-${i}-trade`} tabIndex={-1}>
                        <label className="form-label">Visa category<Req /></label>
                        <SearchSelect value={p.trade} valueLabel={p.trade}
                          options={catOpts} placeholder="Search and select visa category…"
                          onSelect={(_, label) => setPos(i, { trade: label })}
                          extraAction={
                            <button type="button" onClick={() => { setCatForm({ name: '', name_arabic: '', is_active: true }); setAddCatOpen(i) }}
                              className="shrink-0 inline-flex items-center gap-1.5 border border-blue-200 text-blue-700 hover:bg-blue-50 text-sm font-semibold px-3 py-2 rounded-lg">
                              <i className="fa-solid fa-plus" /> New
                            </button>
                          } />
                        <ErrorText path={`positions.${i}.trade`} />
                      </div>
                      <div className="w-full sm:w-24">
                        <label htmlFor={`demand-positions-${i}-quantity`} className="form-label">Quantity<Req /></label>
                        <input {...inputA11y(`positions.${i}.quantity`)} type="number" min={1} value={p.quantity} onChange={(e) => setPos(i, { quantity: e.target.value })} className="form-input w-full" />
                        <ErrorText path={`positions.${i}.quantity`} />
                      </div>
                      <div className="w-full sm:w-32">
                        <label htmlFor={`demand-positions-${i}-salary`} className="form-label">Salary</label>
                        <input {...inputA11y(`positions.${i}.salary`)} type="number" min={0} value={p.salary} onChange={(e) => setPos(i, { salary: e.target.value })} className="form-input w-full" placeholder="—" />
                      </div>
                    </div>
                  </div>
                )
              })}

              <button type="button" onClick={() => setPositions((ps) => [...ps, { ...BLANK_POS }])}
                className="w-full border border-blue-100 bg-blue-50/60 hover:bg-blue-50 text-blue-700 text-sm font-semibold py-2.5 rounded-xl transition">
                <i className="fa-solid fa-plus mr-1.5" /> Add another position
              </button>
            </div>
          )}
        </div>

        {/* Footer nav. `spa-modal-foot` pins this bar to the bottom of the
            scrolling card: step 1 of this wizard is 775px tall inside a 608px
            card at 1024x640, so Cancel/Next used to sit below the fold and had
            to be scrolled to. See the .spa-modal-foot rule in index.css. */}
        <div className="spa-modal-foot px-5 py-4 border-t border-slate-100 flex items-center justify-between">
          {step === 1
            ? <button type="button" onClick={() => setAddOpen(false)} className="px-4 py-2 rounded-lg text-sm font-semibold text-blue-600 border border-blue-200 hover:bg-blue-50">Cancel</button>
            : <button type="button" onClick={() => setStep((s) => s - 1)} className="px-4 py-2 rounded-lg text-sm font-semibold text-blue-600 border border-blue-200 hover:bg-blue-50">Back</button>}
          {step < 3
            ? <button type="button" disabled={step === 1 ? !step1Valid : !step2Valid} onClick={() => setStep((s) => s + 1)}
                className="inline-flex items-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-semibold px-5 py-2 rounded-lg">Next</button>
            : <button type="button" data-testid="demand-submit" aria-busy={saving} disabled={saving || !step3Valid} onClick={submitWizard}
                className="inline-flex min-w-[180px] items-center justify-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-semibold px-5 py-2 rounded-lg">
                {saving ? <><i className="fa-solid fa-spinner fa-spin" /> Creating demand file…</> : 'Create demand file'}
              </button>}
        </div>
      </Modal>

      {/* ══════════ ADD NEW CLIENT (inline sub-modal) ══════════ */}
      <Modal open={addClientOpen} onClose={() => setAddClientOpen(false)} maxWidth="max-w-lg"
        title={<span className="flex items-center gap-2"><i className="fa-regular fa-building text-blue-600" /> Add New Client</span>}>
        <form onSubmit={submitClient} className="p-5 space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="form-label">Client Type</label>
              <select value={clientForm.client_type} onChange={(e) => setClientForm((f) => ({ ...f, client_type: e.target.value }))} className="form-input w-full">
                <option>Individual</option><option>Company</option>
              </select>
            </div>
            <div>
              <label className="form-label">Status</label>
              <select value={clientForm.status} onChange={(e) => setClientForm((f) => ({ ...f, status: e.target.value }))} className="form-input w-full">
                <option value="active">Active</option><option value="inactive">Inactive</option>
              </select>
            </div>
          </div>
          <div>
            <label className="form-label">{clientForm.client_type === 'Company' ? 'Company Name' : 'Full Name'}<Req /></label>
            <input value={clientForm.company_name} onChange={(e) => setClientForm((f) => ({ ...f, company_name: e.target.value }))} className="form-input w-full" required />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="form-label">Email Address</label>
              <input type="email" value={clientForm.email} onChange={(e) => setClientForm((f) => ({ ...f, email: e.target.value }))} className="form-input w-full" placeholder="client@example.com" />
            </div>
            <div>
              <label className="form-label">Phone</label>
              <input value={clientForm.phone} onChange={(e) => setClientForm((f) => ({ ...f, phone: e.target.value }))} className="form-input w-full" placeholder="Enter phone number" />
            </div>
          </div>
          <div>
            <label className="form-label">Street Address</label>
            <input value={clientForm.street} onChange={(e) => setClientForm((f) => ({ ...f, street: e.target.value }))} className="form-input w-full" placeholder="Enter street address" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="form-label">City</label>
              <input value={clientForm.city} onChange={(e) => setClientForm((f) => ({ ...f, city: e.target.value }))} className="form-input w-full" placeholder="City" />
            </div>
            <div>
              <label className="form-label">State</label>
              <input value={clientForm.state} onChange={(e) => setClientForm((f) => ({ ...f, state: e.target.value }))} className="form-input w-full" placeholder="State" />
            </div>
          </div>
          <div>
            <label className="form-label">Country</label>
            <select value={clientForm.country} onChange={(e) => setClientForm((f) => ({ ...f, country: e.target.value }))} className="form-input w-full">
              <option value="">—</option>
              <option>Pakistan</option><option>Saudi Arabia</option><option>United Arab Emirates</option>
              <option>Qatar</option><option>Kuwait</option><option>Oman</option><option>Bahrain</option>
            </select>
          </div>
          <div>
            <div className="text-xs font-bold text-slate-600 mt-1">Opening Balance <span className="font-normal text-slate-400">(Optional)</span></div>
            <div className="grid grid-cols-3 gap-3 mt-1.5">
              <div>
                <label className="form-label">Amount</label>
                <input type="number" min={0} value={clientForm.opening_balance} onChange={(e) => setClientForm((f) => ({ ...f, opening_balance: e.target.value }))} className="form-input w-full" />
              </div>
              <div>
                <label className="form-label">Type</label>
                <select value={clientForm.opening_balance_direction} onChange={(e) => setClientForm((f) => ({ ...f, opening_balance_direction: e.target.value }))} className="form-input w-full">
                  <option value="Owed to us">Debit (Owed to us)</option>
                  <option value="We owe">Credit (We owe)</option>
                </select>
              </div>
              <div>
                <label className="form-label">Balance Date</label>
                <input type="date" value={clientForm.opening_balance_date} onChange={(e) => setClientForm((f) => ({ ...f, opening_balance_date: e.target.value }))} className="form-input w-full" />
              </div>
            </div>
          </div>
          <div>
            <label className="form-label">Notes</label>
            <textarea value={clientForm.notes} onChange={(e) => setClientForm((f) => ({ ...f, notes: e.target.value }))} className="form-input w-full" rows={2} placeholder="Enter notes" />
          </div>
          <div className="grid grid-cols-2 gap-3 pt-2">
            <button type="submit" disabled={clientSaving} className="inline-flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white text-sm font-semibold py-2.5 rounded-lg">
              {clientSaving && <i className="fa-solid fa-spinner fa-spin" />} Create Client
            </button>
            <button type="button" onClick={() => setAddClientOpen(false)} className="py-2.5 rounded-lg text-sm font-semibold text-blue-600 border border-blue-200 hover:bg-blue-50">Cancel</button>
          </div>
        </form>
      </Modal>

      {/* ══════════ ADD VISA CATEGORY (inline sub-modal) ══════════ */}
      <Modal open={addCatOpen !== null} onClose={() => setAddCatOpen(null)} maxWidth="max-w-sm"
        title={<span className="flex items-center gap-2"><i className="fa-solid fa-tags text-blue-600" /> Add Visa Category</span>}>
        <form onSubmit={submitCat} className="p-5 space-y-3">
          <p className="text-xs text-slate-500">Custom categories are visible only to your organization.</p>
          <div>
            <label className="form-label">Name<Req /></label>
            <input autoFocus value={catForm.name} onChange={(e) => setCatForm((f) => ({ ...f, name: e.target.value }))} className="form-input w-full" placeholder="e.g. Electrician" required />
          </div>
          <div>
            <label className="form-label">Name (Arabic)</label>
            <input dir="rtl" value={catForm.name_arabic} onChange={(e) => setCatForm((f) => ({ ...f, name_arabic: e.target.value }))} className="form-input w-full" placeholder="اسم الفئة" />
          </div>
          <div>
            <label className="form-label">Status</label>
            <select value={catForm.is_active ? '1' : '0'} onChange={(e) => setCatForm((f) => ({ ...f, is_active: e.target.value === '1' }))} className="form-input w-full">
              <option value="1">Active</option><option value="0">Inactive</option>
            </select>
          </div>
          <div className="flex items-center justify-end gap-2 pt-2">
            <button type="button" onClick={() => setAddCatOpen(null)} className="px-4 py-2 rounded-lg text-sm font-semibold text-blue-600 border border-blue-200 hover:bg-blue-50">Cancel</button>
            <button type="submit" disabled={catSaving} className="inline-flex items-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white text-sm font-semibold px-5 py-2 rounded-lg">
              {catSaving && <i className="fa-solid fa-spinner fa-spin" />} Create
            </button>
          </div>
        </form>
      </Modal>
    </div>
  )
}
