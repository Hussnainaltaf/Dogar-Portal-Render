import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, apiError } from '@/lib/api'
import { invalidateOptions } from '@/lib/options'
import { useToast } from '@/components/Toast'
import { Modal } from '@/components/ui'
import { humanize } from '@/lib/format'

interface Client { id: number; company_name: string; contact_person?: string; phone?: string; email?: string; country?: string; status?: string; [k: string]: unknown }

const BLANK = {
  company_name: '', client_type: 'Individual', contact_person: '', phone: '',
  email: '', country: '', sponsor_name: '', sponsor_phone: '', sponsor_id: '',
  license_no: '', city: '', status: 'active',
  street: '', state: '', notes: '',
  opening_balance: '0', opening_balance_direction: 'Owed to us',
  opening_balance_date: new Date().toISOString().slice(0, 10),
}

export default function Clients() {
  const toast = useToast()
  const navigate = useNavigate()
  const [rows, setRows] = useState<Client[]>([])
  const [total, setTotal] = useState(0)
  const [q, setQ] = useState('')
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(15)

  // Add Client modal
  const [addOpen, setAddOpen] = useState(false)
  const [form, setForm] = useState({ ...BLANK })
  const [saving, setSaving] = useState(false)

  // FIX 7 (part 2 of 2) — Memoize `load` with useCallback so its identity is
  // stable across renders EXCEPT when the query, page, or pageSize actually
  // change. Previously `load` was a plain function closed over stale q/page/
  // pageSize values, and the eslint-disable-next-line silenced the exhaustive-
  // deps warning that would have caught it. On a fast re-render (e.g. typing
  // quickly into the search box) the debounced setTimeout fired an old closure
  // that queried the wrong page/pageSize, occasionally displaying stale rows.
  // The `toast` reference is intentionally omitted from deps because it's a
  // stable identity from the useToast() provider and re-creating `load` on
  // every render would defeat the debounce.
  const load = useCallback(() => {
    setLoading(true)
    const params: Record<string, string | number> = { limit: pageSize, skip: (page - 1) * pageSize }
    if (q) params.q = q
    api.get('/clients/', { params })
      .then(({ data }) => { setRows(data.items || []); setTotal(data.total || 0) })
      .catch((e) => toast.show(apiError(e), 'error'))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, page, pageSize])

  useEffect(() => {
    const id = window.setTimeout(load, 250)
    return () => window.clearTimeout(id)
  }, [load])
  // Reset to page 1 when the search query changes.
  useEffect(() => { setPage(1) }, [q])

  function openAdd() { setForm({ ...BLANK }); setAddOpen(true) }
  function setF<K extends keyof typeof BLANK>(k: K, v: string) { setForm((f) => ({ ...f, [k]: v })) }

  async function submitClient(e: React.FormEvent) {
    e.preventDefault()
    if (!form.company_name.trim()) { toast.show('Company name is required', 'error'); return }
    setSaving(true)
    try {
      // Strip empty optional fields so backend defaults apply
      const payload: Record<string, string> = {}
      Object.entries(form).forEach(([k, v]) => { if (String(v).trim() !== '') payload[k] = v })
      const { data } = await api.post('/clients/', payload)
      toast.show('Client created', 'success')
      // Any cached client dropdown (Candidates filter, Demand wizard) is stale.
      invalidateOptions('clients')
      setAddOpen(false)
      if (data?.id) navigate(`/clients/${data.id}`)
      else load()
    } catch (err) {
      toast.show(apiError(err), 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 flex items-center gap-2.5"><i className="fa-regular fa-building text-blue-600" /> Clients</h1>
          {/* UX #4 — Show inline spinner beside the count while loading so the
              user gets immediate feedback that the search/page change is in
              flight, instead of a stale count sitting there in silence. */}
          <p className="text-sm text-slate-500 mt-1 flex items-center gap-2" aria-live="polite">
            {loading ? (
              <><i className="fa-solid fa-spinner fa-spin text-blue-600" aria-hidden="true" /><span>Loading…</span></>
            ) : (
              <span>{total.toLocaleString()} clients</span>
            )}
          </p>
        </div>
        <button onClick={openAdd} className="inline-flex items-center gap-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg shadow-sm transition">
          <i className="fa-solid fa-plus" /> Add Client
        </button>
      </div>

      <div className="card"><div className="card-body">
        <div className="relative max-w-md">
          <i className="fa-solid fa-magnifying-glass absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 text-sm" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search company name…" className="form-input w-full pl-9" />
        </div>
      </div></div>

      {/* UX #7 — When paginating, keep the existing rows on-screen and show
          a subtle busy overlay + aria-busy for AT users, instead of blanking
          out the whole table (the previous behaviour caused a distracting
          flash on every page-change click). The initial load still shows the
          full spinner because there is nothing else to display. */}
      <div className="card overflow-hidden"><div className="relative overflow-x-auto table-scroll" aria-busy={loading && rows.length > 0}>
        {loading && rows.length > 0 && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/50 pointer-events-none" role="status" aria-label="Loading">
            <i className="fa-solid fa-spinner fa-spin text-2xl text-blue-600" />
          </div>
        )}
        <table className="data-table responsive-cards w-full">
          <thead><tr><th>Company</th><th>Contact</th><th>Phone</th><th>Country</th><th>Status</th><th className="text-right">Actions</th></tr></thead>
          <tbody>
            {loading && rows.length === 0 ? (
              <tr><td colSpan={6} className="text-center py-12 text-slate-400"><i className="fa-solid fa-spinner fa-spin text-xl text-blue-600" /></td></tr>
            ) : rows.length === 0 ? (
              <tr><td colSpan={6} className="text-center py-12 text-slate-400"><i className="fa-regular fa-building text-3xl block mb-2 opacity-50" />No clients found</td></tr>
            ) : rows.map((c) => (
              <tr key={c.id}>
                <td data-label="Company" data-primary className="font-semibold text-slate-800">{c.company_name}</td>
                <td data-label="Contact">{c.contact_person || '—'}</td>
                <td data-label="Phone" className="font-mono text-[13px]">{c.phone || '—'}</td>
                <td data-label="Country">{c.country || '—'}</td>
                <td data-label="Status"><span className={`badge ${c.status === 'inactive' ? 'badge-inactive' : 'badge-active'}`}>{humanize(c.status) || 'Active'}</span></td>
                <td data-label="Actions" className="text-right"><Link to={`/clients/${c.id}`} className="inline-flex items-center justify-center w-9 h-9 rounded-lg text-blue-600 hover:text-blue-700 hover:bg-blue-50 transition"><i className="fa-regular fa-eye" /></Link></td>
              </tr>
            ))}
          </tbody>
        </table>
        {/* Pagination */}
        <div className="px-4 py-3 border-t border-slate-100 flex items-center justify-between text-xs text-slate-500">
          <div>{total.toLocaleString()} total · Page {page} of {Math.max(1, Math.ceil(total / pageSize))}</div>
          <div className="flex items-center gap-2">
            {/* FIX 7 (part 1 of 2) — Expanded per-page choices. The old list of
                just [10, 15] was inadequate for an enterprise directory: a user
                with 200+ clients had to click Next 13 times to reach the end.
                [10, 15, 25, 50] matches the sizing offered on the demands /
                candidates lists elsewhere in the app for consistency. */}
            <select value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1) }}
              className="border border-slate-200 rounded px-2 py-1 text-xs">
              {[10, 15, 25, 50].map((n) => <option key={n} value={n}>{n} per page</option>)}
            </select>
            <button disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}
              className="px-3 py-1 border border-slate-200 rounded hover:bg-slate-50 disabled:opacity-40">Prev</button>
            <button disabled={page >= Math.ceil(total / pageSize)} onClick={() => setPage((p) => p + 1)}
              className="px-3 py-1 border border-slate-200 rounded hover:bg-slate-50 disabled:opacity-40">Next</button>
          </div>
        </div>
      </div></div>

      {/* ADD NEW CLIENT MODAL — matches reference portal layout */}
      <Modal open={addOpen} onClose={() => setAddOpen(false)} maxWidth="max-w-lg" title={<span className="flex items-center gap-2"><i className="fa-regular fa-building text-blue-600" /> Add New Client</span>}>
        <form onSubmit={submitClient} className="p-5 space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="form-label">Client Type</label>
              <select value={form.client_type} onChange={(e) => setF('client_type', e.target.value)} className="form-input w-full">
                <option value="Individual">Individual</option>
                <option value="Company">Company</option>
              </select>
            </div>
            <div>
              <label className="form-label">Status</label>
              <select value={form.status} onChange={(e) => setF('status', e.target.value)} className="form-input w-full">
                <option value="active">Active</option>
                <option value="inactive">Inactive</option>
              </select>
            </div>
          </div>
          <div>
            <label className="form-label">{form.client_type === 'Company' ? 'Company Name' : 'Full Name'} <span className="text-rose-500">*</span></label>
            <input value={form.company_name} onChange={(e) => setF('company_name', e.target.value)} className="form-input w-full" autoFocus required />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="form-label">Email Address</label>
              <input type="email" value={form.email} onChange={(e) => setF('email', e.target.value)} className="form-input w-full" placeholder="client@example.com" />
            </div>
            <div>
              <label className="form-label">Phone</label>
              <input value={form.phone} onChange={(e) => setF('phone', e.target.value)} className="form-input w-full" placeholder="Enter phone number" />
            </div>
          </div>
          <div>
            <label className="form-label">Street Address</label>
            <input value={form.street} onChange={(e) => setF('street', e.target.value)} className="form-input w-full" placeholder="Enter street address" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="form-label">City</label>
              <input value={form.city} onChange={(e) => setF('city', e.target.value)} className="form-input w-full" placeholder="City" />
            </div>
            <div>
              <label className="form-label">State</label>
              <input value={form.state} onChange={(e) => setF('state', e.target.value)} className="form-input w-full" placeholder="State" />
            </div>
          </div>
          <div>
            <label className="form-label">Country</label>
            <select value={form.country} onChange={(e) => setF('country', e.target.value)} className="form-input w-full">
              <option value="">—</option>
              <option>Pakistan</option><option>Saudi Arabia</option><option>United Arab Emirates</option>
              <option>Qatar</option><option>Kuwait</option><option>Oman</option><option>Bahrain</option>
            </select>
          </div>
          <div>
            <div className="text-xs font-bold text-slate-600 mt-1">Contact &amp; Sponsor <span className="font-normal text-slate-400">(Optional)</span></div>
            <div className="grid grid-cols-2 gap-3 mt-1.5">
              <div>
                <label className="form-label">Contact Person</label>
                <input value={form.contact_person} onChange={(e) => setF('contact_person', e.target.value)} className="form-input w-full" placeholder="Contact person name" />
              </div>
              <div>
                <label className="form-label">License No</label>
                <input value={form.license_no} onChange={(e) => setF('license_no', e.target.value)} className="form-input w-full" placeholder="License number" />
              </div>
            </div>
            <div className="grid grid-cols-3 gap-3 mt-2">
              <div>
                <label className="form-label">Sponsor Name</label>
                <input value={form.sponsor_name} onChange={(e) => setF('sponsor_name', e.target.value)} className="form-input w-full" placeholder="Sponsor name" />
              </div>
              <div>
                <label className="form-label">Sponsor Phone</label>
                <input value={form.sponsor_phone} onChange={(e) => setF('sponsor_phone', e.target.value)} className="form-input w-full" placeholder="Sponsor phone" />
              </div>
              <div>
                <label className="form-label">Sponsor ID</label>
                <input value={form.sponsor_id} onChange={(e) => setF('sponsor_id', e.target.value)} className="form-input w-full" placeholder="Sponsor ID / Iqama" />
              </div>
            </div>
          </div>
          <div>
            <div className="text-xs font-bold text-slate-600 mt-1">Opening Balance <span className="font-normal text-slate-400">(Optional)</span></div>
            <div className="grid grid-cols-3 gap-3 mt-1.5">
              <div>
                <label className="form-label">Amount</label>
                <input type="number" min={0} value={form.opening_balance} onChange={(e) => setF('opening_balance', e.target.value)} className="form-input w-full" />
              </div>
              <div>
                <label className="form-label">Type</label>
                <select value={form.opening_balance_direction} onChange={(e) => setF('opening_balance_direction', e.target.value)} className="form-input w-full">
                  <option value="Owed to us">Debit (Owed to us)</option>
                  <option value="We owe">Credit (We owe)</option>
                </select>
              </div>
              <div>
                <label className="form-label">Balance Date</label>
                <input type="date" value={form.opening_balance_date} onChange={(e) => setF('opening_balance_date', e.target.value)} className="form-input w-full" />
              </div>
            </div>
          </div>
          <div>
            <label className="form-label">Notes</label>
            <textarea value={form.notes} onChange={(e) => setF('notes', e.target.value)} className="form-input w-full" rows={2} placeholder="Enter notes" />
          </div>
          {/* Pinned actions. Measured with scripts/verify_dialog_geometry.py at
              1024x640, "Create Client" sat below the fold and had to be
              scrolled to. `-mx-5 -mb-5 px-5 pb-5` bleeds the form's own p-5
              padding so the sticky bar meets the card edges instead of
              floating over a transparent strip of scrolling fields. */}
          <div className="spa-modal-foot grid grid-cols-2 gap-3 pt-3 border-t border-slate-100 -mx-5 -mb-5 px-5 pb-5 mt-2">
            <button type="submit" disabled={saving} className="inline-flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white text-sm font-semibold py-2.5 rounded-lg">
              {saving && <i className="fa-solid fa-spinner fa-spin" />} Create Client
            </button>
            <button type="button" onClick={() => setAddOpen(false)} className="py-2.5 rounded-lg text-sm font-semibold text-blue-600 border border-blue-200 hover:bg-blue-50">Cancel</button>
          </div>
        </form>
      </Modal>
    </div>
  )
}
