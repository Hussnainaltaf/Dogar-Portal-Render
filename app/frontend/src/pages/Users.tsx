import { useCallback, useEffect, useState } from 'react'
import { api, apiError } from '@/lib/api'
import { useToast } from '@/components/Toast'
import { Modal } from '@/components/ui'

interface UserRow { id: number; username?: string; name: string; email: string; role: string; phone?: string; is_active: boolean }

export default function Users() {
  const toast = useToast()
  const [rows, setRows] = useState<UserRow[]>([])
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState(1)
  const pageSize = 15
  const totalPages = Math.max(1, Math.ceil(rows.length / pageSize))
  const pageRows = rows.slice((page - 1) * pageSize, page * pageSize)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<UserRow | null>(null)
  const [form, setForm] = useState<Record<string, unknown>>({ role: 'staff', is_active: true })
  const [saving, setSaving] = useState(false)

  // FIX 8 — Memoize `load` with useCallback so its identity is stable across
  // renders. Previously `load` was a fresh function on every render and the
  // `useEffect(load, [])` line silenced the exhaustive-deps warning with an
  // eslint-disable. In practice the initial load ran fine, but any future
  // caller that added `load` to a dep array (e.g. a refresh button using
  // useEffect) would have re-run on EVERY render, hammering the /users/
  // endpoint. Wrapping in useCallback makes the intent explicit and satisfies
  // the linter without disabling it.
  const load = useCallback(() => {
    setLoading(true)
    api.get('/users/').then(({ data }) => setRows(data || [])).catch((e) => toast.show(apiError(e), 'error')).finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => { load() }, [load])

  const openNew = () => { setEditing(null); setForm({ role: 'staff', is_active: true }); setModalOpen(true) }
  const openEdit = (u: UserRow) => { setEditing(u); setForm({ ...u }); setModalOpen(true) }

  const save = async () => {
    const name = String(form.name ?? '').trim()
    const email = String(form.email ?? '').trim()
    const password = String(form.password ?? '')
    const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
    if (!name) { toast.show('Please enter the user’s full name.', 'warning'); return }
    if (!email) { toast.show('Please enter an email address.', 'warning'); return }
    if (!EMAIL_RE.test(email)) { toast.show('That email address doesn’t look valid (e.g. name@company.com).', 'warning'); return }
    if (!editing) {
      if (!password) { toast.show('Set a password for the new account.', 'warning'); return }
      if (password.length < 8) { toast.show('Password must be at least 8 characters.', 'warning'); return }
    }
    setSaving(true)
    try {
      const payload = { ...form, name, email }
      if (editing) await api.put(`/users/${editing.id}`, payload)
      else await api.post('/users/', payload)
      toast.show(editing ? 'User updated successfully.' : `Account for “${name}” created.`, 'success'); setModalOpen(false); load()
    } catch (e) { toast.show(apiError(e), 'error') } finally { setSaving(false) }
  }

  const del = async (u: UserRow) => {
    if (!confirm(`Delete user "${u.name}"?`)) return
    try { await api.delete(`/users/${u.id}`); toast.show('User deleted', 'success'); load() }
    catch (e) { toast.show(apiError(e), 'error') }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 flex items-center gap-2.5"><i className="fa-solid fa-user-group text-blue-600" /> Manage Users</h1>
          <p className="text-sm text-slate-500 mt-1">{rows.length} users</p>
        </div>
        <button onClick={openNew} className="btn btn-primary"><i className="fa-solid fa-plus mr-1.5" /> Add User</button>
      </div>

      <div className="card overflow-hidden"><div className="overflow-x-auto table-scroll">
        <table className="data-table responsive-cards w-full">
          <thead><tr><th>Name</th><th>Username</th><th>Email</th><th>Role</th><th>Status</th><th className="text-right">Actions</th></tr></thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={6} className="text-center py-12 text-slate-400"><i className="fa-solid fa-spinner fa-spin text-xl text-blue-600" /></td></tr>
            ) : rows.length === 0 ? (
              <tr><td colSpan={6} className="text-center py-12 text-slate-400"><i className="fa-regular fa-user text-2xl mb-2 block opacity-50" /> No users found</td></tr>
            ) : pageRows.map((u) => (
              <tr key={u.id}>
                <td data-label="Name" data-primary className="font-semibold text-slate-800">{u.name}</td>
                <td data-label="Username" className="font-mono text-[13px]">{u.username || '—'}</td>
                <td data-label="Email">{u.email}</td>
                <td data-label="Role"><span className="badge badge-processing">{u.role}</span></td>
                <td data-label="Status"><span className={`badge ${u.is_active ? 'badge-active' : 'badge-inactive'}`}>{u.is_active ? 'Active' : 'Inactive'}</span></td>
                <td data-label="Actions" className="text-right whitespace-nowrap">
                  <button onClick={() => openEdit(u)} className="inline-flex items-center justify-center w-9 h-9 rounded-lg text-blue-600 hover:text-blue-700 hover:bg-blue-50 transition"><i className="fa-regular fa-pen-to-square" /></button>
                  <button onClick={() => del(u)} className="inline-flex items-center justify-center w-9 h-9 rounded-lg text-rose-500 hover:text-rose-600 hover:bg-rose-50 transition"><i className="fa-regular fa-trash-can" /></button>
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

      <Modal open={modalOpen} onClose={() => setModalOpen(false)} title={<><i className="fa-solid fa-user-group text-blue-600 mr-2" />{editing ? 'Edit User' : 'Add User'}</>} maxWidth="max-w-lg">
        <div className="p-5 space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <Inp label="Name *" k="name" form={form} setForm={setForm} />
            <Inp label="Username" k="username" form={form} setForm={setForm} />
            <Inp label="Email *" k="email" form={form} setForm={setForm} />
            <Inp label="Phone" k="phone" form={form} setForm={setForm} />
            <div>
              <label className="block text-[13px] font-semibold text-slate-700 mb-1.5">Role</label>
              <select value={String(form.role ?? 'staff')} onChange={(e) => setForm({ ...form, role: e.target.value })} className="form-select w-full">
                <option value="admin">Admin</option><option value="staff">Staff</option><option value="agent">Agent</option>
              </select>
            </div>
            {!editing && <Inp label="Password *" k="password" form={form} setForm={setForm} type="password" />}
          </div>
          <label className="flex items-center gap-2 text-sm text-slate-600">
            <input type="checkbox" checked={!!form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} className="w-4 h-4 rounded border-slate-300 text-blue-600" /> Active
          </label>
          <div className="flex justify-end gap-2"><button onClick={() => setModalOpen(false)} className="btn btn-outline">Cancel</button><button onClick={save} disabled={saving} className="btn btn-primary">{saving ? 'Saving…' : 'Save'}</button></div>
        </div>
      </Modal>
    </div>
  )
}

function Inp({ label, k, form, setForm, type = 'text' }: { label: string; k: string; form: Record<string, unknown>; setForm: (f: Record<string, unknown>) => void; type?: string }) {
  return (
    <div>
      <label className="block text-[13px] font-semibold text-slate-700 mb-1.5">{label}</label>
      <input type={type} value={String(form[k] ?? '')} onChange={(e) => setForm({ ...form, [k]: e.target.value })} className="form-input w-full" />
    </div>
  )
}
