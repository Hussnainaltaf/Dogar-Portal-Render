import { useEffect, useState } from 'react'
import { api, apiError } from '@/lib/api'
import { useToast } from '@/components/Toast'
import { Modal } from '@/components/ui'

interface Dep { id: number; first_name?: string; last_name?: string; full_name?: string; cnic?: string; mobile?: string; address?: string }

// Depositors use the dedicated /api/lookups/depositors endpoints.
export default function Depositors() {
  const toast = useToast()
  const [rows, setRows] = useState<Dep[]>([])
  const [q, setQ] = useState('')
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState(1)
  const pageSize = 15
  const totalPages = Math.max(1, Math.ceil(rows.length / pageSize))
  const pageRows = rows.slice((page - 1) * pageSize, page * pageSize)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<Dep | null>(null)
  const [form, setForm] = useState<Record<string, unknown>>({})
  const [saving, setSaving] = useState(false)

  const load = () => {
    setLoading(true)
    api.get('/lookups/depositors', { params: q ? { q } : {} })
      .then(({ data }) => setRows(data.items || [])).catch((e) => toast.show(apiError(e), 'error')).finally(() => setLoading(false))
  }
  useEffect(() => { const id = window.setTimeout(load, 250); return () => window.clearTimeout(id) }, [q]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setPage(1) }, [q])

  const save = async () => {
    if (!form.first_name) { toast.show('First name is required', 'warning'); return }
    setSaving(true)
    try {
      if (editing) await api.put(`/lookups/depositors/${editing.id}`, form)
      else await api.post('/lookups/depositors', form)
      toast.show('Saved', 'success'); setModalOpen(false); load()
    } catch (e) { toast.show(apiError(e), 'error') } finally { setSaving(false) }
  }
  const del = async (d: Dep) => {
    if (!confirm('Delete this depositor?')) return
    try { await api.delete(`/lookups/depositors/${d.id}`); toast.show('Deleted', 'success'); load() } catch (e) { toast.show(apiError(e), 'error') }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><h1 className="text-2xl font-bold text-slate-900 flex items-center gap-2.5"><i className="fa-solid fa-user-tie text-blue-600" /> Depositors</h1><p className="text-sm text-slate-500 mt-1">{rows.length} depositors</p></div>
        <button onClick={() => { setEditing(null); setForm({}); setModalOpen(true) }} className="btn btn-primary"><i className="fa-solid fa-plus mr-1.5" /> Add New</button>
      </div>
      <div className="card"><div className="card-body"><div className="relative max-w-md"><i className="fa-solid fa-magnifying-glass absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 text-sm" /><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search name or CNIC…" className="form-input w-full pl-9" /></div></div></div>
      <div className="card overflow-hidden"><div className="overflow-x-auto table-scroll">
        <table className="data-table responsive-cards w-full">
          <thead><tr><th>Name</th><th>CNIC</th><th>Mobile</th><th>Address</th><th className="text-right">Actions</th></tr></thead>
          <tbody>
            {loading ? <tr><td colSpan={5} className="text-center py-12 text-slate-400"><i className="fa-solid fa-spinner fa-spin text-xl text-blue-600" /></td></tr>
            : rows.length === 0 ? <tr><td colSpan={5} className="text-center py-12 text-slate-400">No depositors found</td></tr>
            : pageRows.map((d) => (
              <tr key={d.id}>
                <td data-label="Name" data-primary className="font-semibold text-slate-800">{d.full_name || `${d.first_name || ''} ${d.last_name || ''}`.trim()}</td>
                <td data-label="CNIC" className="font-mono text-[13px]">{d.cnic || '—'}</td>
                <td data-label="Mobile" className="font-mono text-[13px]">{d.mobile || '—'}</td>
                <td data-label="Address">{d.address || '—'}</td>
                <td data-label="Actions" className="text-right whitespace-nowrap">
                  <button onClick={() => { setEditing(d); setForm({ ...d }); setModalOpen(true) }} className="inline-flex items-center justify-center w-9 h-9 rounded-lg text-blue-600 hover:text-blue-700 hover:bg-blue-50 transition"><i className="fa-regular fa-pen-to-square" /></button>
                  <button onClick={() => del(d)} className="inline-flex items-center justify-center w-9 h-9 rounded-lg text-rose-500 hover:text-rose-600 hover:bg-rose-50 transition"><i className="fa-regular fa-trash-can" /></button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!loading && rows.length > 0 && (
          <div className="px-4 py-3 border-t border-slate-100 flex items-center justify-between text-xs text-slate-500">
            <div>{rows.length.toLocaleString()} total · Page {page} of {totalPages}</div>
            <div className="flex items-center gap-2">
              <button disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}
                className="px-3 py-1 border border-slate-200 rounded hover:bg-slate-50 disabled:opacity-40">Prev</button>
              <button disabled={page >= totalPages} onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                className="px-3 py-1 border border-slate-200 rounded hover:bg-slate-50 disabled:opacity-40">Next</button>
            </div>
          </div>
        )}
      </div></div>
      <Modal open={modalOpen} onClose={() => setModalOpen(false)} title={<><i className="fa-solid fa-user-tie text-blue-600 mr-2" />{editing ? 'Edit Depositor' : 'Add Depositor'}</>} maxWidth="max-w-lg">
        <div className="p-5 space-y-4">
          <div className="grid grid-cols-2 gap-3">
            {[['first_name', 'First Name *'], ['last_name', 'Last Name'], ['cnic', 'CNIC'], ['mobile', 'Mobile']].map(([k, l]) => (
              <div key={k}><label className="block text-[13px] font-semibold text-slate-700 mb-1.5">{l}</label><input value={String(form[k] ?? '')} onChange={(e) => setForm({ ...form, [k]: e.target.value })} className="form-input w-full" /></div>
            ))}
            <div className="col-span-2"><label className="block text-[13px] font-semibold text-slate-700 mb-1.5">Address</label><textarea value={String(form.address ?? '')} onChange={(e) => setForm({ ...form, address: e.target.value })} rows={2} className="form-textarea w-full" /></div>
          </div>
          <div className="flex justify-end gap-2"><button onClick={() => setModalOpen(false)} className="btn btn-outline">Cancel</button><button onClick={save} disabled={saving} className="btn btn-primary">{saving ? 'Saving…' : 'Save'}</button></div>
        </div>
      </Modal>
    </div>
  )
}
