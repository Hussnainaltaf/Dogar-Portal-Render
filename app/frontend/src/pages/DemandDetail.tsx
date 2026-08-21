import { useCallback, useEffect, useState } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { api, apiError } from '@/lib/api'
import { getRows } from '@/lib/options'
import { useToast } from '@/components/Toast'
import { useAuth } from '@/auth/AuthContext'
import { printInFrame } from '@/lib/print'
import { Modal } from '@/components/ui'
import AssignWizard from '@/components/AssignWizard'
import { humanize, cleanValue } from '@/lib/format'
import { useCrumbLabel } from '@/layout/Breadcrumbs'
// Shared rich candidate drawer (5 tabs) — reused verbatim from the Candidates
// page so that "View details" from a demand file opens the EXACT SAME side panel
// in-place, with no navigation to the candidate list first (bug fix 2026-07-10).
import { CandidateDrawer } from './Candidates'

// ───────────────────────── Types (match backend shapes) ─────────────────────
interface Candidate {
  id: number
  assignment_id: number
  full_name: string
  name_arabic?: string
  father_name?: string
  photo?: string
  passport_no?: string
  phone?: string
  status?: string
  status_label?: string
  status_classes?: string
}
interface Trade {
  id: number
  trade: string
  quantity: number
  assigned: number
  available: number
  salary: number
  salary_currency?: string
  contract_years?: number
  notes?: string
  custom_fields?: unknown
  candidates: Candidate[]
}
interface Summary { trades: number; total_slots: number; assigned: number; available: number }
interface LetterheadDoc { name: string; description: string; url: string }

const CURRENCIES = ['SAR', 'AED', 'QAR', 'KWD', 'OMR', 'BHD', 'USD', 'PKR']

function statusClass(s?: string) {
  const v = (s || '').toLowerCase()
  if (v === 'filled') return 'bg-emerald-50 text-emerald-700'
  if (v === 'processing') return 'bg-amber-50 text-amber-700'
  if (v === 'expired' || v === 'cancelled') return 'bg-rose-50 text-rose-700'
  return 'bg-blue-50 text-blue-700'
}
const fmtPKR = (n: unknown) =>
  'PKR ' + (parseFloat(String(n || 0))).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })

type TabKey = 'details' | 'trades' | 'documents' | 'payments'

export default function DemandDetail() {
  const { demandId } = useParams()
  const navigate = useNavigate()
  const toast = useToast()
  const { user } = useAuth()
  const isAgent = user?.role === 'agent'

  const [tab, setTab] = useState<TabKey>('details')
  const [demand, setDemand] = useState<any>(null)
  // Show the real file number in the breadcrumb trail instead of "Demand #7".
  useCrumbLabel(demand?.file_number_display || demand?.file_number)
  const [summary, setSummary] = useState<Summary>({ trades: 0, total_slots: 0, assigned: 0, available: 0 })
  const [trades, setTrades] = useState<Trade[]>([])
  const [docs, setDocs] = useState<LetterheadDoc[]>([])
  const [packetUrl, setPacketUrl] = useState('')
  const [payments, setPayments] = useState<any>({ items: [], total_debit: 0, total_credit: 0, balance: 0 })
  const [loading, setLoading] = useState(true)

  // Modal state
  const [editOpen, setEditOpen] = useState(false)
  const [tradeOpen, setTradeOpen] = useState(false)
  const [editTrade, setEditTradeState] = useState<Trade | null>(null)
  const [payOpen, setPayOpen] = useState(false)
  const [tradeCats, setTradeCats] = useState<string[]>([])

  // Candidate side panel (in-page — opens the shared rich CandidateDrawer by id,
  // no navigation, no iframe). We only need the candidate id; the drawer fetches
  // and renders its own data.
  const [viewCandidateId, setViewCandidateId] = useState<number | null>(null)
  // React AssignWizard (5-step) — create (tradeId) OR edit (editId)
  const [wizard, setWizard] = useState<{ tradeId?: number; tradeName?: string; salary?: number; currency?: string; editId?: number } | null>(null)

  const loadDemand = useCallback(async () => {
    const { data } = await api.get(`/demands/${demandId}`)
    setDemand(data)
  }, [demandId])

  const loadSummary = useCallback(async () => {
    const { data } = await api.get(`/demands/${demandId}/summary`)
    setSummary(data)
  }, [demandId])

  const loadTrades = useCallback(async () => {
    const { data } = await api.get(`/demands/${demandId}/trades`)
    setTrades(data || [])
  }, [demandId])

  const loadDocuments = useCallback(async () => {
    try {
      const { data } = await api.get(`/protector-letters/demand/${demandId}/index`)
      setDocs(data.documents || [])
      setPacketUrl(data.packet_url || '')
    } catch { setDocs([]) }
  }, [demandId])

  const loadPayments = useCallback(async () => {
    try {
      const { data } = await api.get(`/demands/${demandId}/payments`)
      setPayments(data)
    } catch { setPayments({ items: [], total_debit: 0, total_credit: 0, balance: 0 }) }
  }, [demandId])

  // Trade categories come from the shared reference-list cache, so opening
  // several demand files in a row costs one request, not one per file.
  const loadTradeCats = useCallback(async () => {
    try {
      const rows = await getRows('visaCategories')
      setTradeCats(rows.map((v) => v.name))
    } catch { /* ignore */ }
  }, [])

  useEffect(() => {
    Promise.all([loadDemand(), loadSummary(), loadTradeCats()])
      .catch((e) => toast.show(apiError(e), 'error'))
      .finally(() => setLoading(false))
  }, [loadDemand, loadSummary, loadTradeCats, toast])

  // Lazy-load tab data on switch
  useEffect(() => {
    if (tab === 'trades') loadTrades().catch(() => {})
    if (tab === 'documents') loadDocuments().catch(() => {})
    if (tab === 'payments') loadPayments().catch(() => {})
  }, [tab, loadTrades, loadDocuments, loadPayments])

  function closePanel() {
    setViewCandidateId(null)
    // Refresh trade/summary counts in case the drawer edited anything.
    setTimeout(() => { loadTrades().catch(() => {}); loadSummary().catch(() => {}) }, 200)
  }
  function openCandidateFlyout(candidateId: number, mode: 'view' | 'edit') {
    if (mode === 'edit') {
      // EDIT → open the React 5-step wizard in edit mode
      setWizard({ editId: candidateId })
      return
    }
    // VIEW → open the shared rich CandidateDrawer in-place (stays on this page,
    // NO navigation to the candidate list). The drawer fetches its own data and
    // handles its own Esc-to-close / body-scroll-lock / focus trap.
    setViewCandidateId(candidateId)
  }
  function openAssignWizard(tradeId: number, tradeName: string, salary?: number, currency?: string) {
    // CREATE → open the React 5-step Assign Wizard for this trade slot
    setWizard({ tradeId, tradeName, salary, currency })
  }
  // Permission prefix for the wizard Employment step: DTC/LHR/<file>/
  const permissionPrefix = `DTC/LHR/${demand?.file_number || '—'}/`

  // ── Trade actions ──
  async function deleteTrade(id: number) {
    if (!window.confirm('Delete this trade and all assignments?')) return
    try { await api.delete(`/demands/trades/${id}`); await loadTrades(); await loadSummary() }
    catch (e) { toast.show(apiError(e), 'error') }
  }
  async function removeAssign(id: number) {
    if (!window.confirm('Unassign this candidate from the trade?\n\nThis will:\n• Remove the candidate-trade link\n• Reset the candidate workflow stage to "New"\n• Clear any derived document context\n• Record an audit log entry')) return
    try { await api.delete(`/demands/assignments/${id}`); await loadTrades(); await loadSummary() }
    catch (e) { toast.show('Failed to unassign: ' + apiError(e), 'error') }
  }

  async function deletePayment(pid: number) {
    if (!window.confirm('Delete this payment entry? (This also removes it from the client statement.)')) return
    try { await api.delete(`/demands/${demandId}/payments/${pid}`); await loadPayments() }
    catch (e) { toast.show('Failed to delete: ' + apiError(e), 'error') }
  }

  if (loading) return <div className="py-16 text-center text-slate-400"><i className="fa-solid fa-spinner fa-spin text-2xl text-blue-600" /></div>
  if (!demand) return <div className="py-16 text-center text-slate-400">Demand not found</div>

  return (
    <div>
      {/* Back link */}
      <div className="mb-4">
        <Link to="/demands" className="text-sm text-slate-500 hover:text-blue-600 inline-flex items-center gap-1.5">
          <i className="fa-solid fa-arrow-left" /> Back to Demand Files
        </Link>
      </div>

      {/* Header — stacks vertically on mobile so the title, embassy badge and
          action buttons never overflow the viewport (was: fixed flex row that
          pushed the embassy badge + buttons off the right edge on phones). */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between mb-5">
        <div className="min-w-0">
          <h1 className="text-lg sm:text-[22px] font-bold text-slate-800 flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="inline-flex items-center gap-2">
              <i className="fa-regular fa-folder text-blue-600" /> Demand File
            </span>
            <span className="text-blue-600 break-all">{demand.file_number_display || demand.file_number || ''}</span>
          </h1>
          <p className="text-sm text-slate-500 mt-0.5 flex flex-wrap items-center gap-x-1 gap-y-1">
            <span className="inline-flex items-center"><i className="fa-regular fa-building mr-1" />{demand.client_name || '—'}</span>
            <span className="mx-1 hidden sm:inline">·</span>
            <span className="inline-flex items-center"><i className="fa-solid fa-flag mr-1" />{demand.country || '—'}</span>
            <span className="mx-1 hidden sm:inline">·</span>
            <span className={`px-2 py-0.5 rounded-full text-xs ${statusClass(demand.status)}`}>{humanize(demand.status) || 'Active'}</span>
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 shrink-0">
          {demand.embassy && demand.embassy.trim() && (
            <span className="bg-blue-50 text-blue-700 border border-blue-200 text-xs font-semibold px-3 py-1.5 rounded-md">
              <i className="fa-solid fa-building-columns mr-1" /> {demand.embassy}
            </span>
          )}
          {!isAgent && (
            <button onClick={() => setEditOpen(true)} className="bg-white border border-slate-200 hover:bg-slate-50 text-slate-700 text-sm font-semibold px-3.5 py-2 rounded-lg flex items-center gap-2">
              <i className="fa-regular fa-pen-to-square" /> Edit
            </button>
          )}
          <button onClick={() => setTab('documents')} className="bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold px-3.5 py-2 rounded-lg flex items-center gap-2">
            <i className="fa-solid fa-print" /> <span className="whitespace-nowrap">Print Documents</span>
          </button>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-5">
        <div className="stat-tile"><div className="stat-label">Trades</div><div className="stat-value">{summary.trades || 0}</div><div className="stat-sub"><i className="fa-solid fa-briefcase text-slate-400" /> Job categories</div></div>
        <div className="stat-tile"><div className="stat-label">Total Slots</div><div className="stat-value text-blue-600">{summary.total_slots || 0}</div><div className="stat-sub"><i className="fa-solid fa-users text-blue-400" /> Sponsor requirement</div></div>
        <div className="stat-tile"><div className="stat-label">Assigned</div><div className="stat-value text-emerald-600">{summary.assigned || 0}</div><div className="stat-sub"><i className="fa-solid fa-user-check text-emerald-400" /> Candidates placed</div></div>
        <div className="stat-tile"><div className="stat-label">Available</div><div className="stat-value text-amber-600">{summary.available || 0}</div><div className="stat-sub"><i className="fa-solid fa-hourglass-half text-amber-400" /> Open positions</div></div>
      </div>

      {/* Tab bar */}
      <div className="tab-bar mb-4">
        <button className={`tab-btn ${tab === 'details' ? 'active' : ''}`} onClick={() => setTab('details')}><i className="fa-regular fa-circle-question" /> Details</button>
        <button className={`tab-btn ${tab === 'trades' ? 'active' : ''}`} onClick={() => setTab('trades')}><i className="fa-solid fa-briefcase" /> Trades <span className="count">{summary.trades || 0}</span></button>
        <button className={`tab-btn ${tab === 'documents' ? 'active' : ''}`} onClick={() => setTab('documents')}><i className="fa-regular fa-file-lines" /> Documents <span className="count">{docs.length || 0}</span></button>
        <button className={`tab-btn ${tab === 'payments' ? 'active' : ''}`} onClick={() => setTab('payments')}><i className="fa-solid fa-receipt" /> Payments <span className="count">{payments.items?.length || 0}</span></button>
      </div>

      {/* DETAILS TAB */}
      {tab === 'details' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="card">
            <div className="card-header"><h3 className="font-semibold text-slate-800"><i className="fa-regular fa-circle text-blue-600 mr-2" />Basic Information</h3></div>
            <div className="p-5 space-y-3 text-sm">
              <Row label="File Number" value={<span className="text-blue-600 font-semibold">{demand.file_number_display || demand.file_number || '—'}</span>} />
              <Row label="Client" value={demand.client_name} />
              <Row label="Receiving Date" value={demand.receiving_date} />
              <Row label="Permission No." value={demand.permission_no} />
              <Row label="Permission Date" value={demand.permission_date} />
              <Row label="Reference" value={demand.reference} />
              <Row label="Created" value={demand.created_at ? String(demand.created_at).split('T')[0] : ''} />
            </div>
          </div>
          <div className="card">
            <div className="card-header"><h3 className="font-semibold text-slate-800"><i className="fa-solid fa-handshake text-blue-600 mr-2" />Sponsor Information</h3></div>
            <div className="p-5 space-y-3 text-sm">
              <Row label="Sponsor Name" value={demand.sponsor_name} />
              <Row label="Sponsor (Arabic)" value={demand.sponsor_name_arabic ? <span dir="rtl">{demand.sponsor_name_arabic}</span> : null} />
              <Row label="Address" value={demand.sponsor_address} />
              <Row label="Phone" value={demand.sponsor_phone} />
              <Row label="Alt. Phone" value={demand.sponsor_alt_phone} />
            </div>
          </div>
          <div className="card lg:col-span-2">
            <div className="card-header"><h3 className="font-semibold text-slate-800"><i className="fa-regular fa-id-card text-blue-600 mr-2" />Visa Information</h3></div>
            <div className="p-5 grid grid-cols-1 md:grid-cols-3 gap-x-6 gap-y-3 text-sm">
              <GridCell label="Visa Number" value={demand.visa_number} />
              <GridCell label="Bataka Number" value={demand.bataka_number} />
              <GridCell label="Visa Issue Date" value={demand.visa_issue_date} />
              <GridCell label="Hijri Date" value={demand.visa_issue_date_hijri} />
              <GridCell label="Country" value={demand.country} />
              <GridCell label="Embassy" value={demand.embassy} />
              <GridCell label="Document Count" value={demand.document_count} />
            </div>
          </div>
        </div>
      )}

      {/* TRADES TAB */}
      {tab === 'trades' && (
        <div className="card">
          <div className="card-header">
            <h3 className="font-semibold text-slate-800">Trades / Job Categories</h3>
            <button onClick={() => setTradeOpen(true)} className="bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold px-3 py-1.5 rounded-md flex items-center gap-1.5">
              <i className="fa-solid fa-plus" /> Add Trade
            </button>
          </div>
          <div className="p-5 space-y-4">
            {trades.length === 0 ? (
              <div className="text-center text-slate-400 py-10"><i className="fa-solid fa-briefcase text-3xl block mb-2" />No trades added. Click <b>Add Trade</b> to create one.</div>
            ) : trades.map((t) => {
              const fillPct = t.quantity > 0 ? Math.min(100, Math.round((t.assigned / t.quantity) * 100)) : 0
              return (
                <div key={t.id} className="border border-slate-200 rounded-lg overflow-hidden bg-white">
                  <div className="bg-slate-50 px-4 py-3 flex items-center justify-between border-b border-slate-200">
                    <div className="flex items-center gap-3">
                      <div className="w-10 h-10 rounded-lg bg-blue-100 text-blue-600 flex items-center justify-center"><i className="fa-solid fa-briefcase" /></div>
                      <div>
                        <div className="font-semibold text-slate-800">{t.trade}</div>
                        <div className="text-xs text-slate-500 mt-0.5">
                          <i className="fa-solid fa-coins text-amber-500 mr-0.5" /> {t.salary} {t.salary_currency} ·
                          <i className="fa-regular fa-calendar text-slate-400 ml-1" /> {t.contract_years || 2} yrs ·
                          <span className="font-semibold text-slate-700"> {t.assigned}/{t.quantity}</span> filled
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <div className="hidden md:flex items-center gap-2 mr-2">
                        <div className="w-24 h-2 bg-slate-200 rounded-full overflow-hidden"><div className="h-full bg-emerald-500" style={{ width: `${fillPct}%` }} /></div>
                        <span className="text-[11px] text-slate-500 font-semibold">{fillPct}%</span>
                      </div>
                      <div className="flex gap-1.5">
                        {t.candidates.length < t.quantity && (
                          <button onClick={() => openAssignWizard(t.id, t.trade, t.salary, t.salary_currency)} className="bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold px-3 py-1.5 rounded-md flex items-center gap-1.5" title="Assign a candidate to this trade">
                            <i className="fa-solid fa-user-plus" /> Assign
                          </button>
                        )}
                      </div>
                      {!isAgent && (<>
                        <button onClick={() => setEditTradeState(t)} className="bg-white border border-blue-300 text-blue-700 hover:bg-blue-50 text-xs font-semibold px-2.5 py-1.5 rounded-md" title="Edit trade"><i className="fa-regular fa-pen-to-square" /> Edit</button>
                        <button onClick={() => deleteTrade(t.id)} className="bg-white border border-rose-200 text-rose-600 hover:bg-rose-50 text-xs font-semibold px-2.5 py-1.5 rounded-md" title="Delete trade"><i className="fa-regular fa-trash-can" /> Remove</button>
                      </>)}
                    </div>
                  </div>
                  <div className="p-4">
                    {t.candidates.length === 0 ? (
                      <div className="text-center text-slate-400 py-6 border-2 border-dashed border-slate-200 rounded-lg">
                        <i className="fa-regular fa-id-badge text-2xl block mb-1 opacity-40" />
                        <div className="text-xs font-medium">No candidates assigned yet</div>
                        <div className="text-[11px] text-slate-400 mt-1">Use the <span className="font-semibold text-blue-600">Assign</span> button above to add a candidate.</div>
                      </div>
                    ) : (
                      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                        {t.candidates.map((c) => (
                          <CandidateCard key={c.assignment_id} c={c} isAgent={isAgent}
                            onView={() => openCandidateFlyout(c.id, 'view')}
                            onEdit={() => openCandidateFlyout(c.id, 'edit')}
                            onUnassign={() => removeAssign(c.assignment_id)} />
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* DOCUMENTS TAB */}
      {tab === 'documents' && (
        <div className="card mb-4">
          <div className="card-header">
            <h3 className="font-semibold text-slate-800"><i className="fa-regular fa-file-lines text-blue-600 mr-2" />Permission Documents</h3>
            <div className="flex items-center gap-2">
              <button onClick={() => printInFrame(`/api/demands/${demandId}/file-receipt`)} className="text-xs font-semibold bg-blue-600 hover:bg-blue-700 text-white px-3 py-1.5 rounded-md inline-flex items-center gap-1.5" title="Official Demand File Receipt with logo + QR + watermark">
                <i className="fa-solid fa-file-invoice" /> File Receipt
              </button>
              <button onClick={() => packetUrl && printInFrame(packetUrl)} className="text-xs font-semibold bg-blue-600 hover:bg-blue-700 text-white px-3 py-1.5 rounded-md inline-flex items-center gap-1.5">
                <i className="fa-solid fa-print" /> Print All (Packet)
              </button>
            </div>
          </div>
          <div className="p-5">
            <div className="mb-3 px-3 py-2 bg-blue-50 border border-blue-100 rounded-md text-xs text-blue-800">
              <i className="fa-solid fa-circle-info mr-1" />
              Permission documents are rendered on the company letterhead with this demand’s sponsor, visa, and trade data filled in automatically. Print each individually, or use <b>Print All</b> for the full packet.
            </div>
            <div className="divide-y divide-slate-100 border border-slate-200 rounded-lg overflow-hidden">
              {docs.length === 0 ? (
                <div className="text-center text-slate-400 py-6 text-sm">No letterhead documents available.</div>
              ) : docs.map((d, i) => (
                <div key={i} className="flex items-center justify-between px-4 py-3 hover:bg-slate-50">
                  <div className="flex items-center gap-3">
                    <div className="w-10 h-10 rounded-md bg-blue-50 text-blue-700 flex items-center justify-center font-bold">{i + 1}</div>
                    <div>
                      <div className="font-semibold text-slate-800 text-sm">{d.name}</div>
                      <div className="text-[11px] text-slate-500 mt-0.5">{d.description}</div>
                    </div>
                  </div>
                  <button onClick={() => printInFrame(d.url)} className="bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold px-3 py-1.5 rounded-md flex items-center gap-1.5">
                    <i className="fa-solid fa-print" /> Print
                  </button>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* PAYMENTS TAB */}
      {tab === 'payments' && (
        <div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-4">
            <div className="stat-tile"><div className="stat-label">Total Invoiced</div><div className="stat-value text-rose-600">{fmtPKR(payments.total_debit)}</div><div className="stat-sub"><i className="fa-solid fa-file-invoice text-rose-400" /> Charges raised</div></div>
            <div className="stat-tile"><div className="stat-label">Total Received</div><div className="stat-value text-emerald-600">{fmtPKR(payments.total_credit)}</div><div className="stat-sub"><i className="fa-solid fa-hand-holding-dollar text-emerald-400" /> Payments in</div></div>
            <div className="stat-tile"><div className="stat-label">Outstanding Balance</div><div className="stat-value text-amber-600">{fmtPKR(payments.balance)}</div><div className="stat-sub"><i className="fa-solid fa-scale-balanced text-amber-400" /> Debit − Credit</div></div>
          </div>
          <div className="card">
            <div className="card-header">
              <h3 className="font-semibold text-slate-800"><i className="fa-solid fa-receipt text-blue-600 mr-2" />Payment History</h3>
              <button onClick={() => setPayOpen(true)} className="bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold px-3 py-1.5 rounded-md flex items-center gap-1.5">
                <i className="fa-solid fa-plus" /> Add Payment / Invoice
              </button>
            </div>
            <div className="table-scroll">
              <table className="data-table responsive-cards w-full">
                <thead><tr>
                  <th>Date</th><th>Receipt No.</th><th>Type</th><th>Method</th><th>Reference</th>
                  <th className="text-right">Debit (PKR)</th><th className="text-right">Credit (PKR)</th><th>Received By</th><th></th>
                </tr></thead>
                <tbody>
                  {(payments.items || []).length === 0 ? (
                    <tr><td colSpan={9} className="text-center text-slate-400 py-10"><i className="fa-solid fa-receipt text-3xl block mb-2" />No payments recorded. Click <b>Add Payment / Invoice</b> to record one.</td></tr>
                  ) : payments.items.map((p: any) => {
                    const isPay = (p.entry_type || '').toUpperCase() === 'PAYMENT'
                    return (
                      <tr key={p.id}>
                        <td data-label="Date" data-primary>{p.entry_date || '—'}</td>
                        <td data-label="Receipt No." className="font-mono text-xs"><a href={`/api/demands/${demandId}/payments/${p.id}/receipt`} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline font-semibold">{p.receipt_no || '—'}</a></td>
                        <td data-label="Type"><span className={`px-2 py-0.5 rounded-full text-[11px] font-semibold ${isPay ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'}`}>{isPay ? 'PAYMENT' : 'INVOICE'}</span></td>
                        <td data-label="Method">{p.payment_method || '—'}</td>
                        <td data-label="Reference" className="text-xs">{p.reference || <span className="text-slate-300">—</span>}</td>
                        <td data-label="Debit (PKR)" className={`text-right font-mono ${p.debit ? 'text-rose-600 font-semibold' : 'text-slate-300'}`}>{p.debit ? p.debit.toLocaleString(undefined, { minimumFractionDigits: 2 }) : '—'}</td>
                        <td data-label="Credit (PKR)" className={`text-right font-mono ${p.credit ? 'text-emerald-600 font-semibold' : 'text-slate-300'}`}>{p.credit ? p.credit.toLocaleString(undefined, { minimumFractionDigits: 2 }) : '—'}</td>
                        <td data-label="Received By" className="text-xs">{p.received_by || '—'}</td>
                        <td data-label="Actions" className="text-right whitespace-nowrap">
                          <a href={`/api/demands/${demandId}/payments/${p.id}/receipt`} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:bg-blue-50 px-2 py-1 rounded" title="Print receipt"><i className="fa-solid fa-print" /> Print</a>
                          <button onClick={() => deletePayment(p.id)} className="text-rose-500 hover:bg-rose-50 px-2 py-1 rounded" title="Delete"><i className="fa-regular fa-trash-can" /></button>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* MODALS */}
      {editOpen && <EditDemandModal demand={demand} onClose={() => setEditOpen(false)} onSaved={async () => { setEditOpen(false); await loadDemand() }} demandId={demandId!} toast={toast} />}
      {tradeOpen && <TradeModal cats={tradeCats} onClose={() => setTradeOpen(false)} onSaved={async () => { setTradeOpen(false); await loadTrades(); await loadSummary() }} demandId={demandId!} toast={toast} />}
      {editTrade && <EditTradeModal trade={editTrade} cats={tradeCats} onClose={() => setEditTradeState(null)} onSaved={async () => { setEditTradeState(null); await loadTrades() }} toast={toast} />}
      {payOpen && <PaymentModal onClose={() => setPayOpen(false)} onSaved={async (createdId: number | undefined, type: string) => { setPayOpen(false); await loadPayments(); if (createdId && type === 'PAYMENT') printInFrame(`/api/demands/${demandId}/payments/${createdId}/receipt`) }} demandId={demandId!} toast={toast} />}

      {/* CANDIDATE SIDE PANEL — shared rich drawer (5 tabs), in-place, no navigation */}
      {viewCandidateId != null && <CandidateDrawer id={viewCandidateId} onClose={closePanel} />}

      {/* 7-STEP ASSIGN / EDIT WIZARD (React) — matches backend WIZARD_STEPS */}
      {wizard && (
        <AssignWizard
          tradeId={wizard.tradeId}
          tradeName={wizard.tradeName}
          salary={wizard.salary}
          currency={wizard.currency}
          permissionPrefix={permissionPrefix}
          demandId={Number(demandId)}
          editId={wizard.editId}
          onClose={() => setWizard(null)}
          onSaved={() => { loadTrades().catch(() => {}); loadSummary().catch(() => {}) }}
        />
      )}
    </div>
  )
}

// ───────────────────────── Sub-components ─────────────────────────
function safe(value?: React.ReactNode): React.ReactNode {
  return typeof value === 'string' ? cleanValue(value) : value
}
function Row({ label, value }: { label: string; value?: React.ReactNode }) {
  const v = safe(value)
  return (
    <div className="flex justify-between gap-3">
      <span className="text-slate-500">{label}</span>
      <span className="text-slate-800 font-medium text-right">{v || <span className="text-slate-300">—</span>}</span>
    </div>
  )
}
function GridCell({ label, value }: { label: string; value?: React.ReactNode }) {
  const v = safe(value)
  return (
    <div>
      <div className="text-slate-500 text-xs mb-0.5">{label}</div>
      <div className="text-slate-800 font-medium">{v || <span className="text-slate-300">—</span>}</div>
    </div>
  )
}

function CandidateCard({ c, isAgent, onView, onEdit, onUnassign }: { c: Candidate; isAgent: boolean; onView: () => void; onEdit: () => void; onUnassign: () => void }) {
  const [imgErr, setImgErr] = useState(false)
  const initial = (c.full_name || '?')[0].toUpperCase()
  return (
    <div className="border border-slate-200 rounded-lg p-3 hover:border-blue-300 hover:shadow-sm transition bg-white">
      <div className="flex items-start gap-3">
        {c.photo && !imgErr ? (
          <img src={`/static/uploads/photos/${c.photo}`} onError={() => setImgErr(true)} className="w-12 h-12 rounded-full object-cover border border-slate-200" alt="" />
        ) : (
          <div className="w-12 h-12 rounded-full bg-blue-600 text-white font-semibold flex items-center justify-center text-sm">{initial}</div>
        )}
        <div className="flex-1 min-w-0">
          <div className="font-semibold text-slate-800 text-sm truncate">{c.full_name || '—'}</div>
          <div className="text-[11px] text-slate-500 truncate mt-0.5"><span className="text-slate-400">S/O</span> {c.father_name || '—'}</div>
          {c.name_arabic && <div className="text-[11px] text-slate-400" dir="rtl">{c.name_arabic}</div>}
          <div className="mt-1.5">
            <span className={`px-2 py-0.5 rounded-full text-[11px] font-semibold ${c.status_classes || 'bg-slate-100 text-slate-600'}`}>{c.status_label || humanize(c.status) || 'New'}</span>
          </div>
        </div>
      </div>
      <div className="mt-2.5 pt-2.5 border-t border-slate-100 grid grid-cols-2 gap-1 text-[11px] text-slate-500">
        <div><i className="fa-regular fa-id-card mr-1 text-slate-400" />{c.passport_no || '—'}</div>
        <div><i className="fa-solid fa-phone mr-1 text-slate-400" />{c.phone || '—'}</div>
      </div>
      <div className="mt-2.5 flex items-center gap-1.5">
        <button onClick={onView} className="flex-1 text-center text-[11.5px] bg-slate-50 hover:bg-blue-50 hover:text-blue-700 text-slate-700 font-semibold py-1.5 rounded-md border border-slate-200"><i className="fa-regular fa-eye mr-1" /> Details</button>
        <button onClick={onEdit} className="flex-1 text-center text-[11.5px] bg-slate-50 hover:bg-amber-50 hover:text-amber-700 text-slate-700 font-semibold py-1.5 rounded-md border border-slate-200"><i className="fa-regular fa-pen-to-square mr-1" /> Edit</button>
        <button onClick={onView} className="text-blue-600 hover:bg-blue-50 px-2 py-1.5 rounded-md border border-slate-200" title="Open full candidate record"><i className="fa-solid fa-arrow-up-right-from-square" /></button>
        {!isAgent && <button onClick={onUnassign} className="text-rose-500 hover:bg-rose-50 px-2 py-1.5 rounded-md border border-slate-200" title="Unassign"><i className="fa-regular fa-circle-xmark" /></button>}
      </div>
    </div>
  )
}

// ── Edit Demand modal ──
function EditDemandModal({ demand, onClose, onSaved, demandId, toast }: any) {
  const [f, setF] = useState<any>({ ...demand })
  const set = (k: string, v: string) => setF((p: any) => ({ ...p, [k]: v }))
  const save = async () => {
    const payload: any = {}
    const keys = ['file_number', 'reference', 'receiving_date', 'permission_no', 'permission_date', 'status', 'sponsor_name', 'sponsor_name_arabic', 'sponsor_address', 'visa_number', 'bataka_number', 'country', 'embassy']
    keys.forEach((k) => { if (f[k] !== '' && f[k] != null) payload[k] = f[k] })
    try { await api.put(`/demands/${demandId}`, payload); onSaved() }
    catch (e) { toast.show(apiError(e), 'error') }
  }
  return (
    <Modal open onClose={onClose} title="Edit Demand File" maxWidth="max-w-3xl">
      <div className="p-5 space-y-4 text-sm">
        <div className="grid grid-cols-2 gap-3">
          <Lbl t="File Number"><input value={f.file_number || ''} onChange={(e) => set('file_number', e.target.value)} placeholder="DTC/786/<seq>" className="form-input w-full" /></Lbl>
          <Lbl t="Reference"><input value={f.reference || ''} onChange={(e) => set('reference', e.target.value)} className="form-input w-full" /></Lbl>
          <Lbl t="Receiving Date"><input type="date" value={f.receiving_date || ''} onChange={(e) => set('receiving_date', e.target.value)} className="form-input w-full" /></Lbl>
          <Lbl t="Permission No."><input value={f.permission_no || ''} onChange={(e) => set('permission_no', e.target.value)} className="form-input w-full" /></Lbl>
          <Lbl t="Permission Date"><input type="date" value={f.permission_date || ''} onChange={(e) => set('permission_date', e.target.value)} className="form-input w-full" /></Lbl>
          <Lbl t="Status">
            <select value={f.status || 'active'} onChange={(e) => set('status', e.target.value)} className="form-select w-full">
              <option value="active">Active</option><option value="processing">Processing</option><option value="filled">Filled</option><option value="expired">Expired</option><option value="cancelled">Cancelled</option>
            </select>
          </Lbl>
          <Lbl t="Sponsor Name"><input value={f.sponsor_name || ''} onChange={(e) => set('sponsor_name', e.target.value)} className="form-input w-full" /></Lbl>
          <Lbl t="Sponsor Name (Arabic)"><input dir="rtl" value={f.sponsor_name_arabic || ''} onChange={(e) => set('sponsor_name_arabic', e.target.value)} className="form-input w-full" /></Lbl>
          <Lbl t="Sponsor Address" span2><textarea rows={2} value={f.sponsor_address || ''} onChange={(e) => set('sponsor_address', e.target.value)} className="form-textarea w-full" /></Lbl>
          <Lbl t="Visa Number"><input value={f.visa_number || ''} onChange={(e) => set('visa_number', e.target.value)} className="form-input w-full" /></Lbl>
          <Lbl t="Bataka Number"><input value={f.bataka_number || ''} onChange={(e) => set('bataka_number', e.target.value)} className="form-input w-full" /></Lbl>
          <Lbl t="Country"><input value={f.country || ''} onChange={(e) => set('country', e.target.value)} className="form-input w-full" /></Lbl>
          <Lbl t="Embassy"><input value={f.embassy || ''} onChange={(e) => set('embassy', e.target.value)} className="form-input w-full" /></Lbl>
        </div>
        {/* Pinned actions. This dialog is the tallest on the detail page and its
            Cancel button measured below the fold at 1024x640 (sweep2.py).
            The negative `-mx-5 -mb-5` bleeds the bar through the body's p-5
            padding so the sticky bar sits flush with the card edge instead of
            floating above a transparent 20px strip of scrolling content. */}
        <div className="spa-modal-foot flex justify-end gap-2 pt-3 border-t -mx-5 -mb-5 px-5 pb-5 mt-2">
          <button onClick={onClose} className="btn btn-outline">Cancel</button>
          <button onClick={save} className="btn btn-primary">Save</button>
        </div>
      </div>
    </Modal>
  )
}

// ── Add Trade modal ──
function TradeModal({ cats, onClose, onSaved, demandId, toast }: any) {
  const [f, setF] = useState<any>({ trade: '', quantity: 1, contract_years: 2, salary: 0, salary_currency: 'SAR', notes: '' })
  const set = (k: string, v: any) => setF((p: any) => ({ ...p, [k]: v }))
  const save = async () => {
    if (!f.trade) { toast.show('Trade is required', 'warning'); return }
    try {
      await api.post(`/demands/${demandId}/trades`, { ...f, quantity: parseInt(f.quantity) || 1, contract_years: parseInt(f.contract_years) || 2, salary: parseFloat(f.salary) || 0 })
      onSaved()
    } catch (e) { toast.show(apiError(e), 'error') }
  }
  return (
    <Modal open onClose={onClose} title={<><i className="fa-solid fa-briefcase text-blue-600 mr-2" />Add Trade / Visa Category</>} maxWidth="max-w-2xl">
      <div className="p-5 space-y-4 text-sm">
        <Lbl t="Trade / Profession *">
          <select value={f.trade} onChange={(e) => set('trade', e.target.value)} className="form-select w-full">
            <option value="">-- Select Trade --</option>
            {cats.map((c: string) => <option key={c} value={c}>{c}</option>)}
          </select>
        </Lbl>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <Lbl t="Quantity *"><input type="number" min={1} value={f.quantity} onChange={(e) => set('quantity', e.target.value)} className="form-input w-full" /></Lbl>
          <Lbl t="Contract (years)"><input type="number" min={1} value={f.contract_years} onChange={(e) => set('contract_years', e.target.value)} className="form-input w-full" /></Lbl>
          <Lbl t="Salary"><input type="number" step="0.01" value={f.salary} onChange={(e) => set('salary', e.target.value)} className="form-input w-full" /></Lbl>
          <Lbl t="Currency">
            <select value={f.salary_currency} onChange={(e) => set('salary_currency', e.target.value)} className="form-select w-full">
              {CURRENCIES.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </Lbl>
        </div>
        <Lbl t="Notes"><textarea rows={3} value={f.notes} onChange={(e) => set('notes', e.target.value)} className="form-textarea w-full" placeholder="Working hours, accommodation, transport, food, etc. (optional)" /></Lbl>
        <div className="flex justify-end gap-2 pt-3 border-t">
          <button onClick={onClose} className="btn btn-outline">Cancel</button>
          <button onClick={save} className="btn btn-primary"><i className="fa-solid fa-plus mr-1" /> Add Trade</button>
        </div>
      </div>
    </Modal>
  )
}

// ── Edit Trade modal ──
function EditTradeModal({ trade, cats, onClose, onSaved, toast }: any) {
  const [f, setF] = useState<any>({
    trade: trade.trade, quantity: trade.quantity, salary: trade.salary, salary_currency: trade.salary_currency || 'SAR',
    contract_years: trade.contract_years || 2, notes: trade.notes || '',
    custom_fields: trade.custom_fields ? (typeof trade.custom_fields === 'string' ? trade.custom_fields : JSON.stringify(trade.custom_fields, null, 2)) : '',
  })
  const set = (k: string, v: any) => setF((p: any) => ({ ...p, [k]: v }))
  const catOptions = cats.includes(trade.trade) ? cats : [trade.trade, ...cats]
  const save = async () => {
    const payload: any = {
      trade: f.trade, quantity: parseInt(f.quantity) || 1, salary: parseFloat(f.salary) || 0,
      salary_currency: f.salary_currency, contract_years: parseInt(f.contract_years) || 2, notes: f.notes || '',
    }
    const cfRaw = (f.custom_fields || '').trim()
    if (cfRaw) { try { payload.custom_fields = JSON.parse(cfRaw) } catch (e: any) { toast.show('Custom Fields must be valid JSON.\n' + e.message, 'error'); return } }
    else payload.custom_fields = {}
    try { await api.put(`/demands/trades/${trade.id}`, payload); onSaved() }
    catch (e) { toast.show('Failed to update trade: ' + apiError(e), 'error') }
  }
  return (
    <Modal open onClose={onClose} title={<><i className="fa-solid fa-pen-to-square text-amber-600 mr-2" />Edit Trade / Visa Category</>} maxWidth="max-w-2xl">
      <div className="p-5 space-y-4 text-sm">
        <Lbl t="Visa Category / Trade *">
          <select value={f.trade} onChange={(e) => set('trade', e.target.value)} className="form-select w-full">
            {catOptions.map((c: string) => <option key={c} value={c}>{c}</option>)}
          </select>
        </Lbl>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <Lbl t="Quantity"><input type="number" min={1} value={f.quantity} onChange={(e) => set('quantity', e.target.value)} className="form-input w-full" /></Lbl>
          <Lbl t="Salary"><input type="number" step="0.01" value={f.salary} onChange={(e) => set('salary', e.target.value)} className="form-input w-full" /></Lbl>
          <Lbl t="Currency"><select value={f.salary_currency} onChange={(e) => set('salary_currency', e.target.value)} className="form-select w-full">{CURRENCIES.map((c) => <option key={c} value={c}>{c}</option>)}</select></Lbl>
          <Lbl t="Contract (yrs)"><input type="number" min={1} max={10} value={f.contract_years} onChange={(e) => set('contract_years', e.target.value)} className="form-input w-full" /></Lbl>
        </div>
        <Lbl t="Notes"><textarea rows={3} value={f.notes} onChange={(e) => set('notes', e.target.value)} className="form-textarea w-full" /></Lbl>
        <Lbl t="Custom Fields (JSON for data-overlay extra fields)"><textarea rows={3} value={f.custom_fields} onChange={(e) => set('custom_fields', e.target.value)} className="form-textarea w-full font-mono text-xs" placeholder='{"shift_hours": "8", "food": "Provided", "accommodation": "Free"}' /></Lbl>
        <div className="flex justify-end gap-2 pt-3 border-t">
          <button onClick={onClose} className="btn btn-outline">Cancel</button>
          <button onClick={save} className="btn btn-primary"><i className="fa-solid fa-floppy-disk mr-1" /> Save Changes</button>
        </div>
      </div>
    </Modal>
  )
}

// ── Payment / Invoice modal ──
function PaymentModal({ onClose, onSaved, demandId, toast }: any) {
  const [f, setF] = useState<any>({
    entry_type: 'PAYMENT', amount: '', entry_date: new Date().toISOString().slice(0, 10),
    payment_method: 'Cash', reference: '', received_by: '', description: '',
  })
  const set = (k: string, v: any) => setF((p: any) => ({ ...p, [k]: v }))
  const save = async () => {
    const amount = parseFloat(f.amount) || 0
    if (amount <= 0) { toast.show('Amount must be greater than 0', 'warning'); return }
    try {
      const { data } = await api.post(`/demands/${demandId}/payments`, { ...f, amount })
      onSaved(data?.id, f.entry_type)
    } catch (e) { toast.show('Failed to save payment: ' + apiError(e), 'error') }
  }
  return (
    <Modal open onClose={onClose} title={<><i className="fa-solid fa-money-bill-wave text-emerald-600 mr-2" />Record Payment / Invoice</>} maxWidth="max-w-2xl">
      <div className="p-5 space-y-4 text-sm">
        <div>
          <label className="block text-xs font-semibold text-slate-600 mb-2">Entry Type *</label>
          <div className="flex gap-3">
            <label className="flex-1 cursor-pointer">
              <input type="radio" name="entry_type" value="PAYMENT" checked={f.entry_type === 'PAYMENT'} onChange={() => set('entry_type', 'PAYMENT')} className="peer sr-only" />
              <div className={`border-2 rounded-lg p-3 text-center transition ${f.entry_type === 'PAYMENT' ? 'border-emerald-500 bg-emerald-50' : 'border-slate-200'}`}>
                <i className="fa-solid fa-circle-down text-emerald-600 text-xl block mb-1" />
                <div className="font-semibold text-sm text-slate-700">PAYMENT</div>
                <div className="text-[11px] text-slate-500">Money received from client</div>
              </div>
            </label>
            <label className="flex-1 cursor-pointer">
              <input type="radio" name="entry_type" value="INVOICE" checked={f.entry_type === 'INVOICE'} onChange={() => set('entry_type', 'INVOICE')} className="peer sr-only" />
              <div className={`border-2 rounded-lg p-3 text-center transition ${f.entry_type === 'INVOICE' ? 'border-rose-500 bg-rose-50' : 'border-slate-200'}`}>
                <i className="fa-solid fa-file-invoice text-rose-600 text-xl block mb-1" />
                <div className="font-semibold text-sm text-slate-700">INVOICE</div>
                <div className="text-[11px] text-slate-500">Charge raised to client</div>
              </div>
            </label>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Lbl t="Amount (PKR) *"><input type="number" step="0.01" value={f.amount} onChange={(e) => set('amount', e.target.value)} className="form-input w-full font-mono" /></Lbl>
          <Lbl t="Date *"><input type="date" value={f.entry_date} onChange={(e) => set('entry_date', e.target.value)} className="form-input w-full" /></Lbl>
          <Lbl t="Payment Method">
            <select value={f.payment_method} onChange={(e) => set('payment_method', e.target.value)} className="form-select w-full">
              {['Cash', 'Bank Transfer', 'Cheque', 'Online', 'Card', 'Other'].map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </Lbl>
          <Lbl t="Reference / Cheque #"><input value={f.reference} onChange={(e) => set('reference', e.target.value)} placeholder="e.g. TRX-12345" className="form-input w-full" /></Lbl>
        </div>
        <Lbl t="Received By"><input value={f.received_by} onChange={(e) => set('received_by', e.target.value)} placeholder="Cashier / Staff name" className="form-input w-full" /></Lbl>
        <Lbl t="Description / Notes"><textarea rows={2} value={f.description} onChange={(e) => set('description', e.target.value)} placeholder="e.g. Advance payment for visa processing" className="form-textarea w-full" /></Lbl>
        <div className="flex justify-end gap-2 pt-2 border-t">
          <button onClick={onClose} className="btn btn-outline">Cancel</button>
          <button onClick={save} className="btn btn-primary"><i className="fa-solid fa-check mr-1" /> Save Entry</button>
        </div>
      </div>
    </Modal>
  )
}

function Lbl({ t, children, span2 }: { t: string; children: React.ReactNode; span2?: boolean }) {
  return (
    <label className={`block ${span2 ? 'col-span-2' : ''}`}>
      <span className="block text-xs font-semibold text-slate-600 mb-1">{t}</span>
      {children}
    </label>
  )
}
