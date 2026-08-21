import { useEffect, useState } from 'react'
import { api, apiError } from '@/lib/api'
import { useToast } from '@/components/Toast'
import { Modal } from '@/components/ui'

export interface FieldDef { key: string; label: string; type?: 'text' | 'number' | 'checkbox' | 'textarea'; required?: boolean }

interface Props {
  title: string
  icon: string
  endpoint: string // e.g. 'visa-categories'
  fields: FieldDef[]
  columns?: { key: string; label: string }[] // table columns (defaults to fields)
  searchPlaceholder?: string
}

type Row = Record<string, unknown> & { id: number }

// Generic CRUD list used by all lookup pages (visa categories, embassies,
// cities, medical centers, contacts, depositors, service charges). Calls the
// existing /api/lookups/<endpoint> CRUD routes — no backend change.
export default function SimpleListPage({ title, icon, endpoint, fields, columns, searchPlaceholder }: Props) {
  const toast = useToast()
  const [rows, setRows] = useState<Row[]>([])
  const [loading, setLoading] = useState(true)
  const [q, setQ] = useState('')
  const [editing, setEditing] = useState<Row | null>(null)
  const [form, setForm] = useState<Record<string, unknown>>({})
  const [modalOpen, setModalOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(15)
  const cols = columns || fields.map((f) => ({ key: f.key, label: f.label }))
  const totalPages = Math.max(1, Math.ceil(rows.length / pageSize))
  const pageRows = rows.slice((page - 1) * pageSize, page * pageSize)

  const load = () => {
    setLoading(true)
    api.get(`/lookups/${endpoint}`, { params: q ? { q } : {} })
      .then(({ data }) => setRows(data.items || data || []))
      .catch((e) => toast.show(apiError(e), 'error'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    const id = window.setTimeout(load, 250)
    return () => window.clearTimeout(id)
  }, [q]) // eslint-disable-line react-hooks/exhaustive-deps
  // Reset to page 1 when the search query changes.
  useEffect(() => { setPage(1) }, [q])

  const openNew = () => { setEditing(null); setForm({}); setModalOpen(true) }
  const openEdit = (r: Row) => { setEditing(r); setForm({ ...r }); setModalOpen(true) }

  const save = async () => {
    for (const f of fields) {
      if (f.required && !String(form[f.key] ?? '').trim()) { toast.show(`${f.label} is required`, 'warning'); return }
    }
    setSaving(true)
    try {
      if (editing) await api.put(`/lookups/${endpoint}/${editing.id}`, form)
      else await api.post(`/lookups/${endpoint}`, form)
      toast.show(editing ? 'Updated successfully' : 'Created successfully', 'success')
      setModalOpen(false)
      load()
    } catch (e) { toast.show(apiError(e), 'error') } finally { setSaving(false) }
  }

  const del = async (r: Row) => {
    if (!confirm(`Delete "${r[fields[0].key] || 'this item'}"?`)) return
    try { await api.delete(`/lookups/${endpoint}/${r.id}`); toast.show('Deleted', 'success'); load() }
    catch (e) { toast.show(apiError(e), 'error') }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 flex items-center gap-2.5"><i className={`${icon} text-blue-600`} /> {title}</h1>
          <p className="text-sm text-slate-500 mt-1">{rows.length.toLocaleString()} records</p>
        </div>
        <button onClick={openNew} className="btn btn-primary"><i className="fa-solid fa-plus mr-1.5" /> Add New</button>
      </div>

      <div className="card"><div className="card-body">
        <div className="relative max-w-md">
          <i className="fa-solid fa-magnifying-glass absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 text-sm" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={searchPlaceholder || 'Search…'} className="form-input w-full pl-9" />
        </div>
      </div></div>

      <div className="card overflow-hidden"><div className="overflow-x-auto table-scroll">
        <table className="data-table responsive-cards w-full">
          <thead><tr>{cols.map((c) => <th key={c.key}>{c.label}</th>)}<th className="text-right">Actions</th></tr></thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={cols.length + 1} className="text-center py-12 text-slate-400"><i className="fa-solid fa-spinner fa-spin text-xl text-blue-600" /></td></tr>
            ) : rows.length === 0 ? (
              <tr><td colSpan={cols.length + 1} className="text-center py-12 text-slate-400"><i className="fa-regular fa-folder-open text-3xl block mb-2 opacity-50" />No records found</td></tr>
            ) : pageRows.map((r) => (
              <tr key={r.id}>
                {cols.map((c, i) => <td key={c.key} data-label={c.label} {...(i === 0 ? { 'data-primary': '' } : {})}>{typeof r[c.key] === 'boolean' ? (r[c.key] ? 'Yes' : 'No') : (String(r[c.key] ?? '') || '—')}</td>)}
                <td data-label="Actions" className="text-right whitespace-nowrap">
                  <button onClick={() => openEdit(r)} className="inline-flex items-center justify-center w-9 h-9 rounded-lg text-blue-600 hover:text-blue-700 hover:bg-blue-50 transition" title="Edit"><i className="fa-regular fa-pen-to-square" /></button>
                  <button onClick={() => del(r)} className="inline-flex items-center justify-center w-9 h-9 rounded-lg text-rose-500 hover:text-rose-600 hover:bg-rose-50 transition" title="Delete"><i className="fa-regular fa-trash-can" /></button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {/* Pagination */}
        {!loading && rows.length > 0 && (
          <div className="px-4 py-3 border-t border-slate-100 flex items-center justify-between text-xs text-slate-500">
            <div>{rows.length.toLocaleString()} total · Page {page} of {totalPages}</div>
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
        )}
      </div></div>

      <Modal open={modalOpen} onClose={() => setModalOpen(false)} title={<><i className={`${icon} text-blue-600 mr-2`} />{editing ? `Edit ${title}` : `Add ${title}`}</>} maxWidth="max-w-lg">
        <div className="p-5 space-y-4">
          {fields.map((f) => (
            <div key={f.key}>
              <label className="block text-[13px] font-semibold text-slate-700 mb-1.5">{f.label}{f.required && <span className="text-rose-500"> *</span>}</label>
              {f.type === 'checkbox' ? (
                <label className="flex items-center gap-2 text-sm text-slate-600">
                  <input type="checkbox" checked={!!form[f.key]} onChange={(e) => setForm({ ...form, [f.key]: e.target.checked })} className="w-4 h-4 rounded border-slate-300 text-blue-600" /> Active
                </label>
              ) : f.type === 'textarea' ? (
                <textarea value={String(form[f.key] ?? '')} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })} rows={3} className="form-textarea w-full" />
              ) : (
                <input type={f.type === 'number' ? 'number' : 'text'} value={String(form[f.key] ?? '')} onChange={(e) => setForm({ ...form, [f.key]: f.type === 'number' ? Number(e.target.value) : e.target.value })} className="form-input w-full" />
              )}
            </div>
          ))}
          <div className="flex justify-end gap-2 pt-1">
            <button onClick={() => setModalOpen(false)} className="btn btn-outline">Cancel</button>
            <button onClick={save} disabled={saving} className="btn btn-primary">{saving ? <><i className="fa-solid fa-spinner fa-spin mr-1.5" />Saving…</> : 'Save'}</button>
          </div>
        </div>
      </Modal>
    </div>
  )
}
