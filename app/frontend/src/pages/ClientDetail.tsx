import { useCallback, useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { api, apiError } from '@/lib/api'
import { useToast } from '@/components/Toast'
import { printInFrame } from '@/lib/print'
import { humanize } from '@/lib/format'
import { useCrumbLabel } from '@/layout/Breadcrumbs'
import { useDialogKeys } from '@/hooks/useDialogKeys'

/* Full v2-parity Client Detail — hero + 4 stat tiles + 4 tabs
 * (Profile / Contacts / Demand Files / Statement) + edit modal,
 * add-contact modal, add-statement modal, print statement. */

type TabKey = 'profile' | 'contacts' | 'demands' | 'statement'

interface Client {
  id: number
  company_name?: string
  client_type?: string
  status?: string
  license_no?: string
  email?: string
  phone?: string
  sponsor_name?: string
  sponsor_name_arabic?: string
  sponsor_address?: string
  sponsor_address_arabic?: string
  sponsor_phone?: string
  sponsor_alt_phone?: string
  sponsor_id?: string
  street?: string
  city?: string
  state?: string
  country?: string
  address?: string
  contact_person?: string
  opening_balance?: number
  opening_balance_direction?: string
  notes?: string
  created_at?: string
}

interface Summary {
  transactions: number
  total_debit: number
  total_credit: number
  balance: number
  contacts: number
  demand_files: number
}

interface Contact { id: number; name: string; designation?: string; phone?: string; email?: string; note?: string }
interface DemandRow { id: number; file_number?: string; file_number_display?: string; sponsor_name?: string; country?: string; embassy?: string; visa_number?: string; total_slots?: number; assigned?: number; available?: number; status?: string }
interface StatementRow { id: number; entry_date?: string; description?: string; reference?: string; debit?: number; credit?: number; running_balance?: number }

const money = (n?: number) => (n ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })

export function ClientDetail() {
  const { clientId } = useParams()
  const cid = clientId
  const toast = useToast()
  const navigate = useNavigate()

  const [client, setClient] = useState<Client | null>(null)
  const [summary, setSummary] = useState<Summary | null>(null)
  // Replaces the generic "Client #12" breadcrumb with the real company name as
  // soon as the record loads (falls back to the route-derived label until then).
  useCrumbLabel(client?.company_name)
  const [contacts, setContacts] = useState<Contact[]>([])
  const [demands, setDemands] = useState<DemandRow[]>([])
  const [statement, setStatement] = useState<StatementRow[]>([])
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState<TabKey>('profile')
  const [demandQuery, setDemandQuery] = useState('')

  const [editOpen, setEditOpen] = useState(false)
  const [contactModal, setContactModal] = useState<{ open: boolean; edit?: Contact } | null>(null)
  const [stmtOpen, setStmtOpen] = useState(false)

  const loadAll = useCallback(async () => {
    try {
      const [c, s, ct, dm, st] = await Promise.all([
        api.get(`/clients/${cid}`),
        api.get(`/clients/${cid}/summary`).catch(() => ({ data: null })),
        api.get(`/clients/${cid}/contacts`).catch(() => ({ data: { items: [] } })),
        api.get(`/clients/${cid}/demands`).catch(() => ({ data: { items: [] } })),
        api.get(`/clients/${cid}/statement`).catch(() => ({ data: { items: [] } })),
      ])
      setClient(c.data)
      setSummary(s.data)
      setContacts(ct.data.items || ct.data || [])
      setDemands(dm.data.items || dm.data || [])
      setStatement(st.data.items || st.data || [])
    } catch (e) {
      toast.show(apiError(e), 'error')
    } finally {
      setLoading(false)
    }
  }, [cid, toast])

  useEffect(() => { void loadAll() }, [loadAll])

  const filteredDemands = useMemo(() => {
    const q = demandQuery.trim().toLowerCase()
    if (!q) return demands
    return demands.filter((d) =>
      [d.file_number, d.file_number_display, d.sponsor_name, d.country, d.visa_number]
        .some((v) => (v || '').toString().toLowerCase().includes(q)))
  }, [demands, demandQuery])

  const printStatement = () => printInFrame(`/api/clients/${cid}/print-statement`)

  const delClient = async () => {
    if (!confirm('Delete this client? This cannot be undone.')) return
    try {
      await api.delete(`/clients/${cid}`)
      toast.show('Client deleted', 'success')
      navigate('/clients')
    } catch (e) { toast.show(apiError(e), 'error') }
  }

  if (loading) return <div className="py-16 text-center text-slate-400"><i className="fa-solid fa-spinner fa-spin text-2xl text-blue-600" /></div>
  if (!client) return <div className="py-16 text-center text-slate-400">Client not found</div>

  const isActive = (client.status || 'active') !== 'inactive'
  const metaParts = [client.client_type, client.country, client.phone].filter(Boolean)

  return (
    <div className="space-y-4">
      <Link to="/clients" className="text-sm text-blue-600 hover:underline inline-flex items-center gap-1.5">
        <i className="fa-solid fa-arrow-left" /> Back to Clients
      </Link>

      {/* Hero */}
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-[22px] font-bold text-slate-800 flex items-center gap-2">
            <i className="fa-regular fa-building text-blue-600" />
            <span>{client.company_name || '—'}</span>
          </h1>
          <p className="text-sm text-slate-500 mt-0.5">{metaParts.join(' · ') || '—'}</p>
        </div>
        <div className="flex items-center gap-2">
          <span className={`px-2.5 py-1 text-xs font-semibold rounded-full ${isActive ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500'}`}>
            {isActive ? 'Active' : 'Inactive'}
          </span>
          <button onClick={printStatement} className="px-3 py-1.5 text-sm border border-slate-200 rounded-md text-slate-600 hover:bg-slate-50 flex items-center gap-1.5">
            <i className="fa-solid fa-print" /> Print
          </button>
          <button onClick={() => setEditOpen(true)} className="px-3 py-1.5 text-sm border border-slate-200 rounded-md text-slate-600 hover:bg-slate-50 flex items-center gap-1.5">
            <i className="fa-regular fa-pen-to-square" /> Edit
          </button>
          <button onClick={delClient} className="px-3 py-1.5 text-sm border border-rose-200 rounded-md text-rose-600 hover:bg-rose-50 flex items-center gap-1.5">
            <i className="fa-regular fa-trash-can" /> Delete
          </button>
        </div>
      </header>

      {/* Stat tiles */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatTile label="Transactions" value={String(summary?.transactions ?? 0)} sub="Total entries" icon="fa-exchange-alt" iconColor="text-slate-400" />
        <StatTile label="Total Debit" value={money(summary?.total_debit)} sub="Amount owed by client" icon="fa-arrow-down" iconColor="text-rose-400" valueColor="text-rose-600" />
        <StatTile label="Total Credit" value={money(summary?.total_credit)} sub="Amount received" icon="fa-arrow-up" iconColor="text-emerald-400" valueColor="text-emerald-600" />
        <StatTile label="Balance" value={money(summary?.balance)} sub="Net position" icon="fa-scale-balanced" iconColor="text-blue-400" valueColor="text-blue-600" />
      </div>

      {/* Tab bar */}
      <nav className="tab-scroll flex items-center gap-1 border-b border-slate-200" aria-label="Client sections">
        <TabBtn active={tab === 'profile'} onClick={() => setTab('profile')} icon="fa-regular fa-user" label="Profile" />
        <TabBtn active={tab === 'contacts'} onClick={() => setTab('contacts')} icon="fa-regular fa-address-book" label="Contacts" count={contacts.length} />
        <TabBtn active={tab === 'demands'} onClick={() => setTab('demands')} icon="fa-regular fa-folder" label="Demand Files" count={demands.length} />
        <TabBtn active={tab === 'statement'} onClick={() => setTab('statement')} icon="fa-solid fa-receipt" label="Statement" count={statement.length} />
      </nav>

      {/* Profile tab */}
      {tab === 'profile' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <InfoCard icon="fa-regular fa-circle-user" title="Basic Information">
            <Row k="Company / Name" v={client.company_name} />
            <Row k="Type" v={client.client_type} />
            <Row k="License No" v={client.license_no} />
            <Row k="Phone" v={client.phone} />
            <Row k="Email" v={client.email} />
            <Row k="Status" v={isActive ? 'Active' : 'Inactive'} />
          </InfoCard>
          <InfoCard icon="fa-solid fa-location-dot" title="Address">
            <Row k="Street" v={client.street} />
            <Row k="City" v={client.city} />
            <Row k="State / Province" v={client.state} />
            <Row k="Country" v={client.country} />
            <Row k="Address" v={client.address} />
          </InfoCard>
          <InfoCard icon="fa-solid fa-handshake" title="Sponsor Information" span2>
            <Row k="Sponsor Name" v={client.sponsor_name} />
            <Row k="Sponsor Name (Arabic)" v={client.sponsor_name_arabic} rtl />
            <Row k="Sponsor Phone" v={client.sponsor_phone} />
            <Row k="Sponsor Alt Phone" v={client.sponsor_alt_phone} />
            <Row k="Sponsor ID" v={client.sponsor_id} />
            <Row k="Sponsor Address" v={client.sponsor_address} />
            <Row k="Sponsor Address (Arabic)" v={client.sponsor_address_arabic} rtl />
          </InfoCard>
        </div>
      )}

      {/* Contacts tab */}
      {tab === 'contacts' && (
        <div className="card overflow-hidden">
          <div className="card-header flex items-center justify-between">
            <h3 className="font-semibold text-slate-800">Contact Persons</h3>
            <button onClick={() => setContactModal({ open: true })} className="bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold px-3 py-1.5 rounded-md flex items-center gap-1.5">
              <i className="fa-solid fa-plus" /> Add Contact
            </button>
          </div>
          <div className="table-scroll">
            {/* responsive-cards: stacks into labelled cards below the table
                breakpoint so six columns stay readable on a phone. */}
            <table className="data-table responsive-cards w-full">
              <thead><tr><th>Name</th><th>Designation</th><th>Phone</th><th>Email</th><th>Note</th><th className="text-right">Actions</th></tr></thead>
              <tbody>
                {contacts.length === 0 ? <tr><td colSpan={6} className="text-center py-8 text-slate-400">No contacts yet</td></tr>
                : contacts.map((ct) => (
                  <tr key={ct.id}>
                    <td data-label="Name" className="font-medium text-slate-800">{ct.name}</td>
                    <td data-label="Designation">{ct.designation || '—'}</td>
                    <td data-label="Phone">{ct.phone || '—'}</td>
                    <td data-label="Email" className="break-words">{ct.email || '—'}</td>
                    <td data-label="Note" className="text-slate-500">{ct.note || '—'}</td>
                    <td data-label="Actions" className="text-right whitespace-nowrap">
                      <button type="button" onClick={() => setContactModal({ open: true, edit: ct })} className="text-blue-600 px-2" title="Edit" aria-label={`Edit ${ct.name}`}><i className="fa-regular fa-pen-to-square" /></button>
                      <button type="button" aria-label={`Delete ${ct.name}`} onClick={async () => {
                        if (!confirm('Delete this contact?')) return
                        try { await api.delete(`/clients/${cid}/contacts/${ct.id}`); toast.show('Contact deleted', 'success'); void loadAll() }
                        catch (e) { toast.show(apiError(e), 'error') }
                      }} className="text-rose-600 px-2" title="Delete"><i className="fa-regular fa-trash-can" /></button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Demand files tab */}
      {tab === 'demands' && (
        <div className="card overflow-hidden">
          <div className="card-header flex items-center justify-between flex-wrap gap-2">
            <h3 className="font-semibold text-slate-800">Demand Files <span className="text-xs text-slate-500 font-normal">({filteredDemands.length})</span></h3>
            <div className="flex items-center gap-3">
              <div className="relative">
                <i className="fa-solid fa-search absolute left-2.5 top-2 text-slate-400 text-xs" />
                <input value={demandQuery} onChange={(e) => setDemandQuery(e.target.value)} placeholder="Search file / sponsor / visa"
                  className="pl-7 pr-3 py-1.5 text-xs border border-slate-200 rounded-md w-64 focus:outline-none focus:border-blue-500" />
              </div>
              <Link to="/demands" className="text-xs text-blue-600 hover:underline">+ New Demand</Link>
            </div>
          </div>
          <div className="table-scroll">
            <table className="data-table responsive-cards w-full">
              <thead><tr><th>File No</th><th>Sponsor</th><th>Country / Embassy</th><th>Visa No</th><th>Slots</th><th>Status</th><th className="text-right">Open</th></tr></thead>
              <tbody>
                {filteredDemands.length === 0 ? <tr><td colSpan={7} className="text-center py-8 text-slate-400">No demand files</td></tr>
                : filteredDemands.map((d) => (
                  <tr key={d.id}>
                    <td data-label="File No" className="font-mono text-[13px]">{d.file_number_display || d.file_number}</td>
                    <td data-label="Sponsor">{d.sponsor_name || '—'}</td>
                    <td data-label="Country / Embassy" className="text-slate-600">{[d.country, d.embassy].filter(Boolean).join(' · ') || '—'}</td>
                    <td data-label="Visa No" className="font-mono text-[12px]">{d.visa_number || '—'}</td>
                    <td data-label="Slots"><span className="text-slate-700">{d.assigned ?? 0}/{d.total_slots ?? 0}</span></td>
                    <td data-label="Status"><span className="badge badge-processing">{humanize(d.status) || 'Active'}</span></td>
                    <td data-label="Open" className="text-right"><Link to={`/demands/${d.id}`} className="text-blue-600 px-2" aria-label={`Open demand ${d.file_number_display || d.file_number}`}><i className="fa-regular fa-eye" /></Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Statement tab */}
      {tab === 'statement' && (
        <div className="card overflow-hidden">
          <div className="card-header flex items-center justify-between">
            <h3 className="font-semibold text-slate-800">Account Statement</h3>
            <div className="flex items-center gap-2">
              <button onClick={printStatement} className="px-3 py-1.5 text-xs border border-slate-200 rounded-md text-slate-600 hover:bg-slate-50 flex items-center gap-1.5"><i className="fa-solid fa-print" /> Print</button>
              <button onClick={() => setStmtOpen(true)} className="bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold px-3 py-1.5 rounded-md flex items-center gap-1.5"><i className="fa-solid fa-plus" /> Add Entry</button>
            </div>
          </div>
          <div className="table-scroll">
            <table className="data-table responsive-cards w-full">
              <thead><tr><th>Date</th><th>Description</th><th>Reference</th><th className="text-right">Debit</th><th className="text-right">Credit</th><th className="text-right">Balance</th><th className="text-right">Actions</th></tr></thead>
              <tbody>
                {statement.length === 0 ? <tr><td colSpan={7} className="text-center py-8 text-slate-400">No statement entries</td></tr>
                : statement.map((s) => (
                  <tr key={s.id}>
                    <td data-label="Date" className="whitespace-nowrap">{(s.entry_date || '').slice(0, 10) || '—'}</td>
                    <td data-label="Description" className="text-slate-800">{s.description || '—'}</td>
                    <td data-label="Reference" className="text-slate-500">{s.reference || '—'}</td>
                    <td data-label="Debit" className="text-right text-rose-600">{s.debit ? money(s.debit) : '—'}</td>
                    <td data-label="Credit" className="text-right text-emerald-600">{s.credit ? money(s.credit) : '—'}</td>
                    <td data-label="Balance" className="text-right font-medium">{s.running_balance != null ? money(s.running_balance) : '—'}</td>
                    <td data-label="Actions" className="text-right">
                      <button type="button" aria-label="Delete entry" onClick={async () => {
                        if (!confirm('Delete this entry?')) return
                        try { await api.delete(`/clients/${cid}/statement/${s.id}`); toast.show('Entry deleted', 'success'); void loadAll() }
                        catch (e) { toast.show(apiError(e), 'error') }
                      }} className="text-rose-600 px-2" title="Delete"><i className="fa-regular fa-trash-can" /></button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Modals */}
      {editOpen && <EditClientModal client={client} onClose={() => setEditOpen(false)} onSaved={() => { setEditOpen(false); void loadAll() }} cid={cid!} />}
      {contactModal?.open && <ContactModal cid={cid!} edit={contactModal.edit} onClose={() => setContactModal(null)} onSaved={() => { setContactModal(null); void loadAll() }} />}
      {stmtOpen && <StatementModal cid={cid!} onClose={() => setStmtOpen(false)} onSaved={() => { setStmtOpen(false); void loadAll() }} />}
    </div>
  )
}

/* ── small components ───────────────────────────────────────────── */

function StatTile({ label, value, sub, icon, iconColor, valueColor }: { label: string; value: string; sub: string; icon: string; iconColor: string; valueColor?: string }) {
  return (
    <div className="card"><div className="card-body py-3.5">
      <div className="text-[11px] font-semibold text-slate-400 uppercase tracking-wide">{label}</div>
      <div className={`text-2xl font-bold mt-0.5 ${valueColor || 'text-slate-800'}`}>{value}</div>
      <div className="text-[11px] text-slate-400 mt-1 flex items-center gap-1.5"><i className={`fa-solid ${icon} ${iconColor}`} /> {sub}</div>
    </div></div>
  )
}

function TabBtn({ active, onClick, icon, label, count }: { active: boolean; onClick: () => void; icon: string; label: string; count?: number }) {
  return (
    <button onClick={onClick}
      className={`px-4 py-2.5 text-sm font-medium border-b-2 -mb-px flex items-center gap-2 transition ${active ? 'border-blue-600 text-blue-600' : 'border-transparent text-slate-500 hover:text-slate-700'}`}>
      <i className={icon} /> {label}
      {count != null && <span className={`text-[11px] px-1.5 py-0.5 rounded-full ${active ? 'bg-blue-100 text-blue-700' : 'bg-slate-100 text-slate-500'}`}>{count}</span>}
    </button>
  )
}

function InfoCard({ icon, title, span2, children }: { icon: string; title: string; span2?: boolean; children: React.ReactNode }) {
  return (
    <div className={`card overflow-hidden ${span2 ? 'lg:col-span-2' : ''}`}>
      <div className="card-header"><h3 className="font-semibold text-slate-800 flex items-center gap-2"><i className={`${icon} text-blue-600`} />{title}</h3></div>
      <div className={`p-5 text-sm ${span2 ? 'grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-3' : 'space-y-3'}`}>{children}</div>
    </div>
  )
}

function Row({ k, v, rtl }: { k: string; v?: React.ReactNode; rtl?: boolean }) {
  return (
    <div className="flex items-start justify-between gap-4">
      <span className="text-slate-400">{k}</span>
      <span className={`text-slate-800 font-medium text-right ${rtl ? 'dir-rtl' : ''}`} dir={rtl ? 'rtl' : undefined}>{v || '—'}</span>
    </div>
  )
}

/* ── modals ─────────────────────────────────────────────────────── */

function ModalShell({ title, width, onClose, children }: { title: string; width?: string; onClose: () => void; children: React.ReactNode }) {
  // Every ClientDetail modal goes through this shell, so wiring the keyboard
  // behaviour here fixes all of them at once. It had Escape handling, a scroll
  // lock and a focus trap for NONE of them until 2026-08-14 -- it announced
  // aria-modal="true" while Tab walked out into the page behind it.
  const cardRef = useDialogKeys<HTMLDivElement>(true, onClose)
  return createPortal(
    <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm flex items-center justify-center z-[100] p-4 animate-fade-in" onClick={onClose}>
      {/* aria-labelledby points at the <h3> below rather than duplicating the
          title in an aria-label, so the accessible name can never drift from
          the visible one. This shell had no dialog role at all until
          2026-08-13 (found by the [role=dialog] audit in
          scripts/verify_dialog_geometry.py). */}
      <div ref={cardRef} className={`bg-white rounded-xl shadow-2xl ${width || 'w-[560px]'} max-w-full max-h-[90vh] overflow-y-auto`} onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-labelledby="client-modal-title">
        <div className="p-5 border-b flex items-center justify-between sticky top-0 bg-white z-10">
          <h3 id="client-modal-title" className="font-bold text-slate-800">{title}</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-700"><i className="fa-solid fa-xmark" /></button>
        </div>
        {children}
      </div>
    </div>,
    document.body,
  )
}

const inputCls = 'mt-1 w-full border border-slate-200 rounded-md px-3 py-2 focus:outline-none focus:border-blue-500'

function EditClientModal({ client, cid, onClose, onSaved }: { client: Client; cid: string; onClose: () => void; onSaved: () => void }) {
  const toast = useToast()
  const [f, setF] = useState<Client>({ ...client })
  const [saving, setSaving] = useState(false)
  const set = (k: keyof Client, v: string) => setF((p) => ({ ...p, [k]: v }))
  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    try { await api.put(`/clients/${cid}`, f); toast.show('Client updated', 'success'); onSaved() }
    catch (err) { toast.show(apiError(err), 'error') } finally { setSaving(false) }
  }
  return (
    <ModalShell title="Edit Client" width="w-[640px]" onClose={onClose}>
      <form onSubmit={save} className="p-5 grid grid-cols-2 gap-3 text-sm">
        <label className="block"><span className="text-slate-600">Company / Name</span><input value={f.company_name || ''} onChange={(e) => set('company_name', e.target.value)} className={inputCls} /></label>
        <label className="block"><span className="text-slate-600">Phone</span><input value={f.phone || ''} onChange={(e) => set('phone', e.target.value)} className={inputCls} /></label>
        <label className="block"><span className="text-slate-600">Email</span><input value={f.email || ''} onChange={(e) => set('email', e.target.value)} className={inputCls} /></label>
        <label className="block"><span className="text-slate-600">License No</span><input value={f.license_no || ''} onChange={(e) => set('license_no', e.target.value)} className={inputCls} /></label>
        <label className="block"><span className="text-slate-600">Sponsor Name</span><input value={f.sponsor_name || ''} onChange={(e) => set('sponsor_name', e.target.value)} className={inputCls} /></label>
        <label className="block"><span className="text-slate-600">Sponsor Name (Arabic)</span><input dir="rtl" value={f.sponsor_name_arabic || ''} onChange={(e) => set('sponsor_name_arabic', e.target.value)} className={inputCls} /></label>
        <label className="col-span-2 block"><span className="text-slate-600">Sponsor Address</span><textarea rows={2} value={f.sponsor_address || ''} onChange={(e) => set('sponsor_address', e.target.value)} className={inputCls} /></label>
        <label className="block"><span className="text-slate-600">City</span><input value={f.city || ''} onChange={(e) => set('city', e.target.value)} className={inputCls} /></label>
        <label className="block"><span className="text-slate-600">State / Province</span><input value={f.state || ''} onChange={(e) => set('state', e.target.value)} className={inputCls} /></label>
        <label className="block"><span className="text-slate-600">Country</span><input value={f.country || ''} onChange={(e) => set('country', e.target.value)} className={inputCls} /></label>
        <label className="block"><span className="text-slate-600">Status</span>
          <select value={f.status || 'active'} onChange={(e) => set('status', e.target.value)} className={inputCls}>
            <option value="active">Active</option><option value="inactive">Inactive</option>
          </select>
        </label>
        {/* Pinned actions: measured below the fold at 1024x640
            (scripts/verify_dialog_geometry.py). The negative margins bleed the
            form's p-5 padding so the bar reaches the card edges. */}
        <div className="spa-modal-foot col-span-2 flex justify-end gap-2 pt-3 mt-2 border-t -mx-5 -mb-5 px-5 pb-5">
          <button type="button" onClick={onClose} className="px-4 py-2 text-sm text-slate-600 hover:bg-slate-100 rounded-md">Cancel</button>
          <button type="submit" disabled={saving} className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-sm font-semibold px-4 py-2 rounded-md">{saving ? 'Saving…' : 'Save'}</button>
        </div>
      </form>
    </ModalShell>
  )
}

function ContactModal({ cid, edit, onClose, onSaved }: { cid: string; edit?: Contact; onClose: () => void; onSaved: () => void }) {
  const toast = useToast()
  const [f, setF] = useState({ name: edit?.name || '', designation: edit?.designation || '', phone: edit?.phone || '', email: edit?.email || '', note: edit?.note || '' })
  const [saving, setSaving] = useState(false)
  const set = (k: string, v: string) => setF((p) => ({ ...p, [k]: v }))
  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    try {
      if (edit) await api.put(`/clients/${cid}/contacts/${edit.id}`, f)
      else await api.post(`/clients/${cid}/contacts`, f)
      toast.show('Contact saved', 'success'); onSaved()
    } catch (err) { toast.show(apiError(err), 'error') } finally { setSaving(false) }
  }
  return (
    <ModalShell title={edit ? 'Edit Contact' : 'Add Contact'} width="w-[440px]" onClose={onClose}>
      <form onSubmit={save} className="p-5 grid grid-cols-1 gap-3 text-sm">
        <label><span className="text-slate-600">Name *</span><input required value={f.name} onChange={(e) => set('name', e.target.value)} className={inputCls} /></label>
        <label><span className="text-slate-600">Designation</span><input value={f.designation} onChange={(e) => set('designation', e.target.value)} className={inputCls} /></label>
        <label><span className="text-slate-600">Phone</span><input value={f.phone} onChange={(e) => set('phone', e.target.value)} className={inputCls} /></label>
        <label><span className="text-slate-600">Email</span><input type="email" value={f.email} onChange={(e) => set('email', e.target.value)} className={inputCls} /></label>
        <label><span className="text-slate-600">Note</span><textarea rows={2} value={f.note} onChange={(e) => set('note', e.target.value)} className={inputCls} /></label>
        <div className="flex justify-end gap-2 pt-3 mt-2 border-t">
          <button type="button" onClick={onClose} className="px-4 py-2 text-sm text-slate-600 hover:bg-slate-100 rounded-md">Cancel</button>
          <button type="submit" disabled={saving} className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-sm font-semibold px-4 py-2 rounded-md">{saving ? 'Saving…' : 'Save'}</button>
        </div>
      </form>
    </ModalShell>
  )
}

function StatementModal({ cid, onClose, onSaved }: { cid: string; onClose: () => void; onSaved: () => void }) {
  const toast = useToast()
  const [f, setF] = useState({ entry_date: new Date().toISOString().slice(0, 10), description: '', reference: '', debit: '0', credit: '0' })
  const [saving, setSaving] = useState(false)
  const set = (k: string, v: string) => setF((p) => ({ ...p, [k]: v }))
  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    try {
      await api.post(`/clients/${cid}/statement`, { ...f, debit: parseFloat(f.debit) || 0, credit: parseFloat(f.credit) || 0 })
      toast.show('Entry saved', 'success'); onSaved()
    } catch (err) { toast.show(apiError(err), 'error') } finally { setSaving(false) }
  }
  return (
    <ModalShell title="Add Statement Entry" width="w-[480px]" onClose={onClose}>
      <form onSubmit={save} className="p-5 grid grid-cols-2 gap-3 text-sm">
        <label className="col-span-2"><span className="text-slate-600">Date *</span><input type="date" required value={f.entry_date} onChange={(e) => set('entry_date', e.target.value)} className={inputCls} /></label>
        <label className="col-span-2"><span className="text-slate-600">Description *</span><input required value={f.description} onChange={(e) => set('description', e.target.value)} className={inputCls} /></label>
        <label className="col-span-2"><span className="text-slate-600">Reference</span><input value={f.reference} onChange={(e) => set('reference', e.target.value)} className={inputCls} /></label>
        <label><span className="text-slate-600">Debit</span><input type="number" step="0.01" value={f.debit} onChange={(e) => set('debit', e.target.value)} className={inputCls} /></label>
        <label><span className="text-slate-600">Credit</span><input type="number" step="0.01" value={f.credit} onChange={(e) => set('credit', e.target.value)} className={inputCls} /></label>
        <div className="col-span-2 flex justify-end gap-2 pt-3 mt-2 border-t">
          <button type="button" onClick={onClose} className="px-4 py-2 text-sm text-slate-600 hover:bg-slate-100 rounded-md">Cancel</button>
          <button type="submit" disabled={saving} className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-sm font-semibold px-4 py-2 rounded-md">{saving ? 'Saving…' : 'Save Entry'}</button>
        </div>
      </form>
    </ModalShell>
  )
}

export default ClientDetail
