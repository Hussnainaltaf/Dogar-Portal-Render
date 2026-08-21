import { useCallback, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { Link } from 'react-router-dom'
import { api, apiError } from '@/lib/api'
import { useToast } from '@/components/Toast'
import { ImageUpload } from '@/components/ImageUpload'
import { useDialogKeys } from '@/hooks/useDialogKeys'
import { FONT_STACKS, resolveFontStack } from '@/lib/branding'
import { BrandingSettings } from '@/routes/_app.settings.index'

// ─────────────────────────── Login History ───────────────────────────
export function LoginHistory() {
  const toast = useToast()
  const [rows, setRows] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    api.get('/users/login-history').then(({ data }) => setRows(data.items || [])).catch((e) => toast.show(apiError(e), 'error')).finally(() => setLoading(false))
  }, [toast])
  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-bold text-slate-900 flex items-center gap-2.5"><i className="fa-regular fa-clock text-blue-600" /> Login History</h1>
      <div className="card overflow-hidden"><div className="table-scroll">
        {/* `responsive-cards` + per-cell data-label turns this into a stacked
            card list below the table breakpoint (see app.css) instead of a
            horizontally-scrolling grid nobody can read on a phone. */}
        <table className="data-table responsive-cards w-full">
          <thead><tr><th>Email</th><th>Status</th><th>IP Address</th><th>When</th></tr></thead>
          <tbody>
            {loading ? <tr><td colSpan={4} className="text-center py-12 text-slate-400"><i className="fa-solid fa-spinner fa-spin text-xl text-blue-600" /></td></tr>
            : rows.length === 0 ? <tr><td colSpan={4} className="text-center py-12 text-slate-400">No history</td></tr>
            : rows.map((r) => (
              <tr key={r.id}>
                <td data-label="Email">{r.email}</td>
                <td data-label="Status"><span className={`badge ${r.status === 'Success' ? 'badge-active' : 'badge-rejected'}`}>{r.status}</span></td>
                <td data-label="IP Address" className="font-mono text-[13px]">{r.ip_address || '—'}</td>
                <td data-label="When" className="text-slate-500 text-[13px]">{r.occurred_at ? new Date(r.occurred_at).toLocaleString() : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div></div>
    </div>
  )
}

// ─────────────────────────── Roles (real RBAC editor) ───────────────────────────
type Role = { id: number; name: string; description?: string; is_system?: boolean; permissions: string[]; created_at?: string }
type CatItem = { key: string; label: string }
type CatModule = { module: string; items: CatItem[] }
type Catalog = { catalog: CatModule[]; presets: Record<string, string[]>; all_keys: string[] }

export function Roles() {
  const toast = useToast()
  const [rows, setRows] = useState<Role[]>([])
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState<Role | null>(null)   // role being edited (or blank for new)
  const [saving, setSaving] = useState(false)
  // Escape / scroll-lock / focus trap. This dialog had NONE of them until
  // 2026-08-14: it announced aria-modal="true" while Tab walked straight out
  // into the page behind it, and a keyboard-only operator could not dismiss it
  // at all. `closeOnEscape: !saving` -- dismissing mid-save would abandon a
  // half-written role.
  const roleCardRef = useDialogKeys<HTMLDivElement>(!!editing, () => setEditing(null), { closeOnEscape: !saving })

  const reload = () =>
    api.get('/users/roles')
      .then(({ data }) => setRows(data || []))
      .catch((e) => toast.show(apiError(e), 'error'))

  // BUGFIX (audit 2026-08-09): the two initial requests were chained with
  // Promise.all().finally(setLoading(false)). That coupling caused two
  // real, user-visible defects observed in production:
  //   1. If EITHER request hung, `.finally` never fired and the roles grid
  //      spun forever ("infinite loading spinner").
  //   2. If the roles list resolved but the catalog request failed, the
  //      whole Promise.all rejected — the modal (gated on `catalog`) could
  //      never open its permission matrix, so "Customize Role" showed only
  //      the Role Name + Description fields with no checklists.
  // Fix: load the two resources INDEPENDENTLY. The list controls the
  // spinner and clears it on its own settle; the catalog loads in parallel
  // and, if it fails, is retried lazily when the editor opens (see openEdit
  // / openNew). Neither can wedge the other.
  const loadCatalog = useCallback(() =>
    api.get('/users/roles/_meta/catalog')
      .then(({ data }) => { setCatalog(data as Catalog); return data as Catalog })
      .catch(() => null), [])

  useEffect(() => {
    let alive = true
    api.get('/users/roles')
      .then(({ data }) => { if (alive) setRows(data || []) })
      .catch((e) => { if (alive) toast.show(apiError(e), 'error') })
      .finally(() => { if (alive) setLoading(false) })
    loadCatalog()
    return () => { alive = false }
  }, [toast, loadCatalog])

  // Opening the editor lazily (re)loads the permission catalog if a previous
  // attempt failed, so the matrix always fills in rather than showing a modal
  // that is stuck with only the name/description fields.
  const openNew = () => { if (!catalog) loadCatalog(); setEditing({ id: 0, name: '', description: '', is_system: false, permissions: [] }) }
  const openEdit = (r: Role) => { if (!catalog) loadCatalog(); setEditing({ ...r, permissions: [...(r.permissions || [])] }) }

  const del = async (r: Role) => {
    if (r.is_system) { toast.show('System roles cannot be deleted', 'error'); return }
    if (!confirm(`Delete role "${r.name}"? This cannot be undone.`)) return
    try {
      await api.delete(`/users/roles/${r.id}`)
      toast.show('Role deleted', 'success')
      reload()
    } catch (e) { toast.show(apiError(e), 'error') }
  }

  const save = async () => {
    if (!editing) return
    const name = (editing.name || '').trim()
    if (!name) { toast.show('Role name is required', 'error'); return }
    const isAdmin = name.toLowerCase() === 'admin'
    let perms = editing.permissions
    if (isAdmin && !perms.includes('*')) perms = ['*', ...perms]
    setSaving(true)
    try {
      if (editing.id) {
        // System roles: send only editable fields (name is locked server-side)
        const body: any = { description: editing.description || '', permissions: perms }
        if (!editing.is_system) body.name = name
        await api.put(`/users/roles/${editing.id}`, body)
        toast.show('Role updated', 'success')
      } else {
        await api.post('/users/roles', { name, description: editing.description || '', permissions: perms })
        toast.show('Role created', 'success')
      }
      setEditing(null)
      reload()
    } catch (e) { toast.show(apiError(e), 'error') }
    finally { setSaving(false) }
  }

  const isWildcard = (editing?.permissions || []).includes('*')
  const totalKeys = catalog?.all_keys.length ?? 0

  const togglePerm = (key: string) => {
    if (!editing) return
    const set = new Set(editing.permissions)
    if (set.has(key)) set.delete(key); else set.add(key)
    setEditing({ ...editing, permissions: Array.from(set) })
  }
  const toggleModule = (mod: CatModule, on: boolean) => {
    if (!editing) return
    const set = new Set(editing.permissions)
    mod.items.forEach((it) => { if (on) set.add(it.key); else set.delete(it.key) })
    setEditing({ ...editing, permissions: Array.from(set) })
  }
  const applyPreset = (preset: string) => {
    if (!editing || !catalog) return
    setEditing({ ...editing, permissions: [...(catalog.presets[preset] || [])] })
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-slate-900 flex items-center gap-2.5"><i className="fa-solid fa-shield-halved text-blue-600" /> Roles &amp; Access Control</h1>
        <button onClick={openNew} className="btn btn-primary inline-flex items-center gap-2">
          <i className="fa-solid fa-plus" /> Add New Role
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {loading ? <div className="col-span-3 text-center py-12 text-slate-400"><i className="fa-solid fa-spinner fa-spin text-xl text-blue-600" /></div>
        : rows.length === 0 ? <div className="col-span-3 text-center py-12 text-slate-400">No roles yet</div>
        : rows.map((r) => {
          const count = (r.permissions || []).includes('*') ? 'All' : (r.permissions || []).length
          return (
            <div key={r.id} className="card group">
              <div className="card-body">
                <div className="flex items-center justify-between mb-2">
                  <h3 className="font-bold text-slate-800 capitalize flex items-center gap-2">
                    <i className={`fa-solid ${r.is_system ? 'fa-lock text-amber-500' : 'fa-user-shield text-blue-500'}`} />
                    {r.name}
                  </h3>
                  {r.is_system && <span className="badge badge-processing">System</span>}
                </div>
                <p className="text-sm text-slate-500 mb-3 min-h-[2.5rem]">{r.description || 'No description'}</p>
                <div className="flex items-center justify-between">
                  <span className="text-[11px] font-semibold text-slate-400 uppercase">{count} {count === 'All' ? 'access' : 'permissions'}</span>
                  <div className="flex items-center gap-2 opacity-80 group-hover:opacity-100 transition-opacity">
                    <button onClick={() => openEdit(r)} className="text-blue-600 hover:text-blue-800 text-sm font-semibold inline-flex items-center gap-1">
                      <i className="fa-solid fa-sliders" /> Customize
                    </button>
                    {!r.is_system && (
                      <button onClick={() => del(r)} className="text-red-500 hover:text-red-700 text-sm">
                        <i className="fa-solid fa-trash" />
                      </button>
                    )}
                  </div>
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {/* PORTAL TO <body> -- do not inline this overlay back into the page tree.
          `position: fixed` resolves against the nearest ancestor that has a
          transform / filter / will-change / contain, NOT necessarily the
          viewport. The routed content sits inside `.page-enter`, whose entry
          animation asserted exactly such a transform, so this dialog was laid
          out and CLIPPED inside a 736x180 wrapper: cut off mid-form with
          'Save Changes' unreachable (reported 2026-08-13). The CSS was fixed
          too, but portalling is the durable guarantee -- it makes the overlay
          immune to ANY future ancestor that creates a containing block. */}
      {editing && createPortal(
        <div className="fixed inset-0 z-[100] flex items-start justify-center bg-black/40 p-4 overflow-y-auto" onClick={() => setEditing(null)}>
          {/* role/aria-modal/aria-label: this dialog had NONE of them until
              2026-08-13, so assistive tech announced it as an anonymous div and
              scripts/verify_dialog_geometry.py could not even find it (it
              locates dialogs by [role=dialog], which is how the omission was
              noticed). The shared <Modal> in components/ui.tsx has always set
              these; hand-rolled overlays must match it. */}
          <div ref={roleCardRef} className="bg-white rounded-2xl shadow-2xl w-full max-w-3xl my-8 max-h-[calc(100vh-4rem)] overflow-y-auto" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Customize role">
            {/* Header */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200">
              <h2 className="text-lg font-bold text-slate-900 flex items-center gap-2">
                <i className="fa-solid fa-user-shield text-blue-600" />
                {editing.id ? `Customize Role — ${editing.name}` : 'Add New Role'}
              </h2>
              <button onClick={() => setEditing(null)} className="text-slate-400 hover:text-slate-700"><i className="fa-solid fa-xmark text-xl" /></button>
            </div>

            {/* Body */}
            <div className="px-6 py-4 space-y-4 max-h-[70vh] overflow-y-auto">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-semibold text-slate-700 mb-1">Role Name</label>
                  <input
                    className="form-input w-full"
                    value={editing.name}
                    disabled={editing.is_system}
                    placeholder="e.g. Finance Officer"
                    onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                  />
                  {editing.is_system && <p className="text-[11px] text-amber-600 mt-1">System role name is locked.</p>}
                </div>
                <div>
                  <label className="block text-sm font-semibold text-slate-700 mb-1">Description</label>
                  <input
                    className="form-input w-full"
                    value={editing.description || ''}
                    placeholder="Short description"
                    onChange={(e) => setEditing({ ...editing, description: e.target.value })}
                  />
                </div>
              </div>

              {/* Presets — only shown once the permission catalog is loaded.
                  If the catalog request is still in flight (or failed and is
                  being retried) we render a graceful placeholder below instead
                  of hiding the whole modal. */}
              {catalog && (
              <div className="flex items-center flex-wrap gap-2 border-y border-slate-100 py-3">
                <span className="text-xs font-semibold text-slate-500 uppercase mr-1">Quick set:</span>
                {Object.keys(catalog.presets).map((p) => (
                  <button key={p} onClick={() => applyPreset(p)} className="text-xs px-2.5 py-1 rounded-full bg-slate-100 hover:bg-blue-100 text-slate-700 hover:text-blue-800 capitalize font-medium">
                    {p} default
                  </button>
                ))}
                <button onClick={() => setEditing({ ...editing, permissions: [] })} className="text-xs px-2.5 py-1 rounded-full bg-slate-100 hover:bg-red-100 text-slate-700 hover:text-red-700 font-medium">
                  Clear all
                </button>
                <span className="ml-auto text-xs font-semibold text-slate-500">
                  {isWildcard ? 'Full access (*)' : `${editing.permissions.length} / ${totalKeys} selected`}
                </span>
              </div>
              )}

              {isWildcard && (
                <div className="rounded-lg bg-amber-50 border border-amber-200 px-4 py-3 text-sm text-amber-800 flex items-center gap-2">
                  <i className="fa-solid fa-crown" /> This role has the wildcard <code className="font-mono">*</code> permission — full access to every module.
                </div>
              )}

              {/* Catalog still loading (or briefly failed) — never leave the
                  editor as a bare two-field form; show progress + retry. */}
              {!catalog && (
                <div className="rounded-xl border border-slate-200 px-4 py-8 text-center text-sm text-slate-500 space-y-3">
                  <div><i className="fa-solid fa-spinner fa-spin text-blue-600 mr-2" /> Loading permission options…</div>
                  <button type="button" onClick={() => loadCatalog()} className="text-xs px-3 py-1.5 rounded-full bg-slate-100 hover:bg-blue-100 text-slate-700 hover:text-blue-800 font-medium">
                    <i className="fa-solid fa-rotate-right mr-1" /> Retry
                  </button>
                </div>
              )}

              {/* Permission matrix */}
              {catalog && (
              <div className={`space-y-3 ${isWildcard ? 'opacity-50 pointer-events-none' : ''}`}>
                {catalog.catalog.map((mod) => {
                  const modKeys = mod.items.map((i) => i.key)
                  const allOn = modKeys.every((k) => editing.permissions.includes(k))
                  const someOn = modKeys.some((k) => editing.permissions.includes(k))
                  return (
                    <div key={mod.module} className="rounded-xl border border-slate-200 overflow-hidden">
                      <div className="flex items-center justify-between px-4 py-2.5 bg-slate-50 border-b border-slate-100">
                        <h4 className="font-semibold text-slate-800 text-sm">{mod.module}</h4>
                        <label className="inline-flex items-center gap-2 text-xs font-medium text-slate-500 cursor-pointer">
                          <input
                            type="checkbox"
                            className="rounded border-slate-300 text-blue-600"
                            checked={allOn}
                            ref={(el) => { if (el) el.indeterminate = someOn && !allOn }}
                            onChange={(e) => toggleModule(mod, e.target.checked)}
                          />
                          Select all
                        </label>
                      </div>
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1.5 px-4 py-3">
                        {mod.items.map((it) => (
                          <label key={it.key} className="inline-flex items-center gap-2.5 text-sm text-slate-700 cursor-pointer py-0.5">
                            <input
                              type="checkbox"
                              className="rounded border-slate-300 text-blue-600"
                              checked={editing.permissions.includes(it.key)}
                              onChange={() => togglePerm(it.key)}
                            />
                            <span>{it.label}</span>
                          </label>
                        ))}
                      </div>
                    </div>
                  )
                })}
              </div>
              )}
            </div>

            {/* Footer */}
            <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-slate-200 bg-slate-50 rounded-b-2xl">
              <button onClick={() => setEditing(null)} className="btn btn-outline">Cancel</button>
              <button onClick={save} disabled={saving} className="btn btn-primary inline-flex items-center gap-2">
                {saving ? <i className="fa-solid fa-spinner fa-spin" /> : <i className="fa-solid fa-check" />}
                {editing.id ? 'Save Changes' : 'Create Role'}
              </button>
            </div>
          </div>
        </div>,
        document.body,
      )}
    </div>
  )
}

// ─────────────────────────── Documents ───────────────────────────
// Moved to its own file (full v2-parity: System Templates grid +
// filterable table with configure/print/delete). Re-export for App.tsx.
export { Documents } from './Documents'

// REMOVED 2026-07-30 (operator request): the `Reports` page, its ReportTab
// type and the REPORT_DEFS table that drove its four tabs. The operator
// reported the page as unusable; it was deleted rather than hidden, along
// with the /reports route, the reports.html legacy template and the
// reports:view / reports:export permissions. See section 4.11 of
// PROJECT_DOCUMENTATION.md.

// ─────────────────────────── Settings ───────────────────────────
type Field = { key: string; label: string; full?: boolean; type?: string }
type SettingsSection = { title: string; icon: string; fields: Field[] }

const SETTINGS_SECTIONS: SettingsSection[] = [
  {
    title: 'Company Information', icon: 'fa-building',
    fields: [
      { key: 'company_name', label: 'Company Name' },
      { key: 'company_name_arabic', label: 'Company Name (Arabic)' },
      { key: 'owner_name', label: 'Owner / Proprietor' },
      { key: 'address', label: 'Address', full: true },
      { key: 'address_arabic', label: 'Address (Arabic)', full: true },
    ],
  },
  {
    title: 'Contact Details', icon: 'fa-address-book',
    fields: [
      { key: 'phone', label: 'Phone' },
      { key: 'mobile', label: 'Mobile' },
      { key: 'fax', label: 'Fax' },
      { key: 'email', label: 'Email', type: 'email' },
      { key: 'website', label: 'Website' },
    ],
  },
  {
    title: 'Licensing & Files', icon: 'fa-certificate',
    fields: [
      { key: 'oep_license_number', label: 'OEP License Number' },
      { key: 'file_prefix', label: 'File Prefix' },
      { key: 'starting_point', label: 'File Starting Point', type: 'number' },
    ],
  },
]

export function Settings() {
  const toast = useToast()
  const [form, setForm] = useState<Record<string, unknown>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [theme, setTheme] = useState<string>(() => localStorage.getItem('theme') || 'corporate-blue')
  const [activeTab, setActiveTab] = useState<'general' | 'branding'>('general')

  useEffect(() => {
    api.get('/settings/general').then(({ data }) => setForm(data)).catch((e) => toast.show(apiError(e), 'error')).finally(() => setLoading(false))
  }, [toast])

  const applyTheme = (t: string) => {
    setTheme(t)
    localStorage.setItem('theme', t)
    document.documentElement.classList.toggle('theme-forest-green', t === 'forest-green')
    toast.show(`Theme switched to ${t === 'forest-green' ? 'Forest Green' : 'Corporate Blue'}`, 'success')
  }

  const save = async () => {
    setSaving(true)
    try { await api.put('/settings/general', form); toast.show('Settings saved successfully', 'success') }
    catch (e) { toast.show(apiError(e), 'error') } finally { setSaving(false) }
  }

  const setField = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }))

  return (
    <div className="space-y-5 max-w-4xl">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold text-slate-900 flex items-center gap-2.5">
          <i className="fa-solid fa-gear text-blue-600" /> General Settings
        </h1>
        {activeTab === 'general' && <button onClick={save} disabled={saving || loading} className="btn btn-primary inline-flex items-center gap-2 disabled:opacity-50">
          {saving ? <><i className="fa-solid fa-spinner fa-spin" /> Saving…</> : <><i className="fa-solid fa-floppy-disk" /> Save Changes</>}
        </button>}
      </div>

      <div className="flex items-center gap-1 border-b border-slate-200" role="tablist" aria-label="Settings sections">
        <button type="button" role="tab" aria-selected={activeTab === 'general'} onClick={() => setActiveTab('general')} className={`px-4 py-2.5 text-sm font-semibold border-b-2 transition ${activeTab === 'general' ? 'border-blue-600 text-blue-700' : 'border-transparent text-slate-500 hover:text-slate-700'}`}>
          <i className="fa-solid fa-gear mr-2" />General
        </button>
        <button type="button" role="tab" aria-selected={activeTab === 'branding'} onClick={() => setActiveTab('branding')} className={`px-4 py-2.5 text-sm font-semibold border-b-2 transition ${activeTab === 'branding' ? 'border-blue-600 text-blue-700' : 'border-transparent text-slate-500 hover:text-slate-700'}`}>
          <i className="fa-solid fa-palette mr-2" />Branding
        </button>
      </div>

      {activeTab === 'branding' ? (
        <BrandingSettings />
      ) : loading ? (
        <div className="card"><div className="card-body text-center py-16 text-slate-400"><i className="fa-solid fa-spinner fa-spin text-2xl text-blue-600" /></div></div>
      ) : (
        <>
          {/* Appearance / theme switcher */}
          <div className="card"><div className="card-body">
            <h3 className="font-bold text-slate-800 mb-1 flex items-center gap-2"><i className="fa-solid fa-palette text-blue-600" /> Appearance</h3>
            <p className="text-sm text-slate-500 mb-4">Choose the brand colour theme for the portal.</p>
            <div className="grid grid-cols-2 gap-4 max-w-md">
              {([
                { id: 'corporate-blue', label: 'Corporate Blue', swatch: '#2563eb' },
                { id: 'forest-green', label: 'Forest Green', swatch: '#16a34a' },
              ] as const).map((opt) => (
                <button
                  key={opt.id}
                  onClick={() => applyTheme(opt.id)}
                  className={`flex items-center gap-3 rounded-lg border-2 p-3 text-left transition ${
                    theme === opt.id ? 'border-blue-600 bg-blue-50' : 'border-slate-200 hover:border-slate-300'
                  }`}
                >
                  <span className="w-8 h-8 rounded-full shrink-0 ring-2 ring-white shadow" style={{ backgroundColor: opt.swatch }} />
                  <span className="min-w-0">
                    <span className="block text-sm font-semibold text-slate-800">{opt.label}</span>
                    {theme === opt.id && <span className="block text-[11px] text-blue-600 font-medium"><i className="fa-solid fa-check mr-1" />Active</span>}
                  </span>
                </button>
              ))}
            </div>
          </div></div>

          {/* Field sections */}
          {SETTINGS_SECTIONS.map((sec) => (
            <div key={sec.title} className="card"><div className="card-body space-y-4">
              <h3 className="font-bold text-slate-800 flex items-center gap-2"><i className={`fa-solid ${sec.icon} text-blue-600`} /> {sec.title}</h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {sec.fields.filter((f) => f.key in form).map((f) => (
                  <div key={f.key} className={f.full ? 'sm:col-span-2' : ''}>
                    <label className="block text-[13px] font-semibold text-slate-700 mb-1.5">{f.label}</label>
                    <input
                      type={f.type || 'text'}
                      value={String(form[f.key] ?? '')}
                      onChange={(e) => setField(f.key, e.target.value)}
                      className="form-input w-full"
                    />
                  </div>
                ))}
              </div>
            </div></div>
          ))}

          {/* Read-only account info */}
          <div className="card"><div className="card-body space-y-4">
            <h3 className="font-bold text-slate-800 flex items-center gap-2"><i className="fa-solid fa-circle-info text-blue-600" /> Account Information</h3>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-sm">
              <div><p className="text-[11px] font-semibold text-slate-400 uppercase tracking-wide">Subdomain</p><p className="font-medium text-slate-700 mt-0.5">{String(form.subdomain ?? '—')}</p></div>
              <div><p className="text-[11px] font-semibold text-slate-400 uppercase tracking-wide">Plan</p><p className="font-medium text-slate-700 mt-0.5">{String(form.plan ?? '—')}</p></div>
              <div><p className="text-[11px] font-semibold text-slate-400 uppercase tracking-wide">Status</p><p className="mt-0.5"><span className="badge badge-active capitalize">{String(form.status ?? '—')}</span></p></div>
            </div>
          </div></div>

          <div className="flex justify-end">
            <button onClick={save} disabled={saving} className="btn btn-primary inline-flex items-center gap-2 disabled:opacity-50">
              {saving ? <><i className="fa-solid fa-spinner fa-spin" /> Saving…</> : <><i className="fa-solid fa-floppy-disk" /> Save Changes</>}
            </button>
          </div>
        </>
      )}
    </div>
  )
}

// ─────────────────────────── Protector Letter ───────────────────────────
// Replicates the legacy portal workflow (index.php?route=protector.formN&ids=…):
//  1. Type a passport number → "Get Data" fetches candidate + permission info.
//  2. Rows accumulate in a table (multiple candidates per letter batch).
//  3. Green print buttons render each letter on the company letterhead:
//     Main Letter (Registration of FSAs), Undertaking, Undertaking-B, Packet.
export function ProtectorLetter() {
  const toast = useToast()
  const [passport, setPassport] = useState('')
  const [rows, setRows] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [printing, setPrinting] = useState('')
  // Optional letter overrides (Ref/date etc. — renderer falls back to demand data)
  const [letterDate, setLetterDate] = useState('')
  const [fileNumber, setFileNumber] = useState('')

  const addPassport = async () => {
    const pn = passport.trim()
    if (!pn) return
    if (rows.some((r) => (r.passport_no || '').toUpperCase() === pn.toUpperCase())) {
      toast.show('Passport already in the list', 'error'); return
    }
    setLoading(true)
    try {
      const { data } = await api.get(`/protector-letters/lookup/${encodeURIComponent(pn)}`)
      setRows((prev) => [...prev, data])
      setPassport('')
    } catch (e) { toast.show(apiError(e, 'No record found for this passport'), 'error') }
    finally { setLoading(false) }
  }

  const removeRow = (id: number) => setRows((prev) => prev.filter((r) => r.id !== id))

  const printLetter = async (kind: 'main' | 'undertaking' | 'undertaking-b' | 'packet') => {
    if (rows.length === 0) { toast.show('Add at least one passport first', 'error'); return }
    setPrinting(kind)
    try {
      const ids = rows.map((r) => r.id).join(',')
      const params = new URLSearchParams({ ids })
      if (letterDate) params.set('letter_date', letterDate)
      if (fileNumber) params.set('file_number', fileNumber)
      const { printInFrame } = await import('@/lib/print')
      printInFrame(`/api/protector-letters/print/${kind}?${params.toString()}`, () => setPrinting(''))
      setTimeout(() => setPrinting(''), 4000)
    } catch { setPrinting('') }
  }

  const btn = (kind: 'main' | 'undertaking' | 'undertaking-b' | 'packet', label: string, icon: string) => (
    <button onClick={() => printLetter(kind)} disabled={!!printing || rows.length === 0}
      className="btn text-white bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50">
      {printing === kind ? <i className="fa-solid fa-spinner fa-spin mr-1.5" /> : <i className={`fa-solid ${icon} mr-1.5`} />}
      {label}
    </button>
  )

  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-bold text-slate-900 flex items-center gap-2.5">
        <i className="fa-regular fa-envelope-open text-blue-600" /> Protector Letters
      </h1>

      {/* Step 1: add passports */}
      <section className="card"><div className="card-body space-y-4">
        <h2 className="text-[15px] font-bold text-slate-800"><i className="fa-solid fa-passport text-blue-500 mr-2" />Add Passports</h2>
        <div className="flex flex-wrap items-end gap-2">
          <div className="flex-1 min-w-[220px]">
            <label className="block text-[13px] font-semibold text-slate-700 mb-1">Passport Number</label>
            <input id="protector-passport-input" value={passport} onChange={(e) => setPassport(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && addPassport()}
              placeholder="e.g. WC4128943 — press Enter" className="form-input w-full font-mono" />
          </div>
          <div>
            <label className="block text-[13px] font-semibold text-slate-700 mb-1">Letter Date <span className="text-slate-400 font-normal">(optional)</span></label>
            <input type="date" value={letterDate} onChange={(e) => setLetterDate(e.target.value)} className="form-input" />
          </div>
          <div>
            <label className="block text-[13px] font-semibold text-slate-700 mb-1">File / Ref No <span className="text-slate-400 font-normal">(optional)</span></label>
            <input value={fileNumber} onChange={(e) => setFileNumber(e.target.value)} placeholder="Ref No" className="form-input w-36" />
          </div>
          <button onClick={addPassport} disabled={loading} className="btn btn-primary h-[38px]">
            {loading ? <i className="fa-solid fa-spinner fa-spin" /> : <><i className="fa-solid fa-magnifying-glass mr-1.5" />Get Data</>}
          </button>
        </div>
      </div></section>

      {/* Step 2: selected candidates table */}
      <section className="card overflow-hidden">
        <div className="px-5 py-3 border-b border-slate-100 flex items-center justify-between">
          <h2 className="text-[15px] font-bold text-slate-800"><i className="fa-solid fa-list-check text-blue-500 mr-2" />Selected Candidates</h2>
          <span className="text-[13px] text-slate-500 font-semibold">{rows.length} candidate{rows.length !== 1 ? 's' : ''}</span>
        </div>
        <div className="table-scroll">
          <table className="data-table responsive-cards w-full">
            <thead><tr><th>S.No</th><th>Passport No</th><th>Name</th><th>Father Name</th><th>Permission No. &amp; Date</th><th>Category</th><th>Sponsor</th><th></th></tr></thead>
            <tbody>
              {rows.length === 0 ? (
                <tr><td colSpan={8} className="text-center py-10 text-slate-400">Enter a passport number above and press <b>Get Data</b> to build the letter list</td></tr>
              ) : rows.map((r, i) => (
                <tr key={r.id}>
                  <td data-label="S.No" className="text-slate-500">{i + 1}</td>
                  <td data-label="Passport No" className="font-mono text-[13px] font-semibold">{r.passport_no}</td>
                  <td data-label="Name" className="font-semibold text-slate-800">{r.full_name}</td>
                  <td data-label="Father Name">{r.father_name || '—'}</td>
                  <td data-label="Permission No. & Date" className="text-[13px]">{r.permission_display || '—'}</td>
                  <td data-label="Category" className="text-[13px]">{r.profession || '—'}</td>
                  <td data-label="Sponsor" className="text-[13px]">{r.sponsor_name || '—'}</td>
                  <td data-label="Actions"><button type="button" onClick={() => removeRow(r.id)} className="text-red-500 hover:text-red-700" title="Remove" aria-label={`Remove ${r.full_name}`}><i className="fa-solid fa-xmark" /></button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Step 3: print letter buttons (legacy green buttons) */}
      <section className="card"><div className="card-body">
        <h2 className="text-[15px] font-bold text-slate-800 mb-3"><i className="fa-solid fa-print text-blue-500 mr-2" />Print Letters</h2>
        <div className="flex flex-wrap gap-2">
          {btn('main', 'Registration Letter (FSA)', 'fa-file-lines')}
          {btn('undertaking', 'Undertaking', 'fa-file-signature')}
          {btn('undertaking-b', 'Undertaking-B', 'fa-file-contract')}
          {btn('packet', 'Print Full Packet', 'fa-layer-group')}
        </div>
        <p className="text-[12px] text-slate-400 mt-3">
          Letters render on the company letterhead exactly like the legacy portal (Registration of Foreign Service
          Agreements, Undertaking &amp; Undertaking-B) and open the print dialog automatically.
        </p>
      </div></section>
    </div>
  )
}

// ─────────────────────────── Portal Admin (super-admin command centre) ───────────────────────────
export function PortalAdmin() {
  const toast = useToast()
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [showProvision, setShowProvision] = useState(false)
  const [saving, setSaving] = useState(false)
  const [customizing, setCustomizing] = useState<any>(null)
  const blank = { company_name: '', short_name: '', subtitle: 'Overseas Employment Promoters', admin_name: '', admin_email: '', admin_password: '', contact_phone: '', plan: 'standard', primary_color: '#2563eb', notes: '' }
  const [form, setForm] = useState<any>(blank)
  const set = (k: string, v: any) => setForm((f: any) => ({ ...f, [k]: v }))
  // See the note on the Customize Role dialog above -- same missing keyboard
  // half, same fix. Provisioning creates a whole tenant database, so Escape is
  // disabled while the request is in flight.
  const provisionCardRef = useDialogKeys<HTMLDivElement>(showProvision, () => setShowProvision(false), { closeOnEscape: !saving })

  const load = () => {
    setLoading(true)
    api.get('/tenants/_meta/overview').then(({ data }) => setData(data)).catch((e) => toast.show(apiError(e), 'error')).finally(() => setLoading(false))
  }
  useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t) }, []) // eslint-disable-line

  const provision = async () => {
    if (!form.company_name.trim() || !form.admin_name.trim() || !form.admin_email.trim() || form.admin_password.length < 6) {
      toast.show('Company name, admin name, admin email and a password (6+ chars) are required', 'error'); return
    }
    setSaving(true)
    try {
      const { data: t } = await api.post('/tenants', form)
      toast.show(`Portal "${t.company_name}" provisioned — subdomain slug: ${t.slug}`, 'success')
      setShowProvision(false); setForm(blank); load()
    } catch (e) { toast.show(apiError(e, 'Provisioning failed'), 'error') } finally { setSaving(false) }
  }

  const setStatus = async (t: any, status: string) => {
    try { await api.put(`/tenants/${t.id}`, { status }); toast.show(`${t.short_name || t.company_name} ${status === 'active' ? 'activated' : 'suspended'}`, 'success'); load() }
    catch (e) { toast.show(apiError(e), 'error') }
  }

  const totals = data?.totals
  const portals: any[] = data?.portals || []
  const activity: any[] = data?.activity || []
  const kindIcon = (k: string) => k === 'login' ? 'fa-right-to-bracket text-blue-500' : k === 'demand' ? 'fa-folder-open text-amber-500' : k === 'candidate' ? 'fa-user text-emerald-500' : 'fa-circle-dot text-slate-400'

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold text-slate-900 flex items-center gap-2.5"><i className="fa-solid fa-sitemap text-blue-600" /> Portal Admin</h1>
        <div className="flex gap-2">
          <button onClick={load} className="btn btn-secondary"><i className={`fa-solid fa-rotate mr-1.5 ${loading ? 'fa-spin' : ''}`} />Refresh</button>
          <button onClick={() => setShowProvision(true)} className="btn btn-primary"><i className="fa-solid fa-plus mr-1.5" />Provision New Portal</button>
        </div>
      </header>

      {/* Totals strip */}
      <section className="grid grid-cols-2 md:grid-cols-6 gap-3">
        {[['Portals', totals?.portals, 'fa-sitemap', 'text-blue-600'], ['Clients', totals?.clients, 'fa-building', 'text-indigo-600'],
          ['Demand Files', totals?.demands, 'fa-folder-open', 'text-amber-600'], ['Candidates', totals?.candidates, 'fa-users', 'text-emerald-600'],
          ['Candidates / 7d', totals?.candidates_this_week, 'fa-user-plus', 'text-teal-600'], ['Demands / 7d', totals?.demands_this_week, 'fa-folder-plus', 'text-orange-600'],
        ].map(([label, val, icon, color]: any) => (
          <div key={label} className="card"><div className="card-body py-3 px-4 flex items-center gap-3">
            <i className={`fa-solid ${icon} ${color} text-lg`} />
            <div><div className="text-xl font-bold text-slate-900">{loading && val == null ? '…' : (val ?? 0).toLocaleString()}</div>
            <div className="text-[11px] font-semibold text-slate-500 uppercase">{label}</div></div>
          </div></div>
        ))}
      </section>

      <div className="grid lg:grid-cols-3 gap-5">
        {/* Portals grid */}
        <section className="lg:col-span-2 card overflow-hidden">
          <div className="px-5 py-3 border-b border-slate-100"><h2 className="text-[15px] font-bold text-slate-800"><i className="fa-solid fa-server text-blue-500 mr-2" />Portal Copies (Tenants)</h2></div>
          <div className="table-scroll">
            <table className="data-table responsive-cards w-full">
              <thead><tr><th>Portal</th><th>Slug</th><th>Clients</th><th>Demands</th><th>Candidates</th><th>DB Size</th><th>Status</th><th></th></tr></thead>
              <tbody>
                {loading && portals.length === 0 ? <tr><td colSpan={8} className="text-center py-12 text-slate-400"><i className="fa-solid fa-spinner fa-spin text-xl text-blue-600" /></td></tr>
                : portals.map((p) => (
                  <tr key={p.slug}>
                    <td data-label="Portal" className="font-semibold text-slate-800">
                      {p.company_name}
                      {p.is_control && <span className="ml-2 text-[10px] font-bold text-white bg-blue-600 rounded px-1.5 py-0.5">CONTROL</span>}
                    </td>
                    <td data-label="Slug" className="font-mono text-[13px]">{p.slug}</td>
                    <td data-label="Clients">{p.stats?.clients?.toLocaleString() ?? '—'}</td>
                    <td data-label="Demands">{p.stats?.demands?.toLocaleString() ?? '—'}</td>
                    <td data-label="Candidates">{p.stats?.candidates?.toLocaleString() ?? '—'}</td>
                    <td data-label="DB Size" className="text-[13px] text-slate-500">{p.stats?.db_size_kb ? `${(p.stats.db_size_kb / 1024).toFixed(1)} MB` : '—'}</td>
                    <td data-label="Status"><span className={`badge ${p.status === 'active' ? 'badge-active' : 'badge-inactive'}`}>{p.status}</span></td>
                    <td data-label="Actions">
                      {!p.is_control && (
                        <div className="flex items-center gap-3 whitespace-nowrap">
                          {p.status === 'active' && (
                            <a
                              href={`/t/${p.slug}/`}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-emerald-600 hover:text-emerald-800 text-[13px] font-semibold"
                              title={`Open ${p.company_name}'s isolated tenant portal in a new tab`}
                            >
                              <i className="fa-solid fa-arrow-up-right-from-square mr-1" />Open Tenant
                            </a>
                          )}
                          <button type="button" onClick={() => setCustomizing(p)} className="text-blue-600 hover:text-blue-800 text-[13px] font-semibold" title="Customize branding &amp; domain"><i className="fa-solid fa-palette mr-1" />Customize</button>
                          {p.status === 'active'
                            ? <button type="button" onClick={() => setStatus(p, 'suspended')} className="text-amber-600 hover:text-amber-800 text-[13px] font-semibold" title="Suspend"><i className="fa-solid fa-pause mr-1" />Suspend</button>
                            : <button type="button" onClick={() => setStatus(p, 'active')} className="text-emerald-600 hover:text-emerald-800 text-[13px] font-semibold" title="Activate"><i className="fa-solid fa-play mr-1" />Activate</button>}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        {/* Real-time activity feed */}
        <section className="card overflow-hidden">
          <div className="px-5 py-3 border-b border-slate-100 flex items-center justify-between">
            <h2 className="text-[15px] font-bold text-slate-800"><i className="fa-solid fa-wave-square text-emerald-500 mr-2" />Live Activity</h2>
            <span className="text-[11px] text-slate-400">auto-refreshes 30s</span>
          </div>
          <ul className="divide-y divide-slate-50 max-h-[420px] overflow-y-auto">
            {activity.length === 0 ? <li className="py-10 text-center text-slate-400 text-sm">No recent activity</li>
            : activity.map((a, i) => (
              <li key={i} className="px-4 py-2.5 flex items-start gap-2.5">
                <i className={`fa-solid ${kindIcon(a.kind)} mt-0.5 text-sm`} />
                <div className="min-w-0">
                  <p className="text-[13px] text-slate-700 truncate">{a.text}</p>
                  <p className="text-[11px] text-slate-400">{a.portal} · {a.when ? new Date(a.when.replace(' ', 'T') + 'Z').toLocaleString() : ''}</p>
                </div>
              </li>
            ))}
          </ul>
        </section>
      </div>

      {/* Provision modal */}
      {/* Portalled for the same reason as the role dialog above: `fixed` was
          resolving against the animated `.page-enter` wrapper, so this form was
          clipped and 'Provision Portal' could not be reached. */}
      {showProvision && createPortal(
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4" onClick={() => !saving && setShowProvision(false)}>
          {/* See the role/aria note on the Customize Role dialog above. */}
          <div ref={provisionCardRef} className="bg-white rounded-xl shadow-2xl w-full max-w-lg max-h-[calc(100vh-2rem)] overflow-y-auto" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Provision new portal copy">
            <div className="px-5 py-4 border-b border-slate-100 flex items-center justify-between">
              <h3 className="font-bold text-slate-900"><i className="fa-solid fa-rocket text-blue-600 mr-2" />Provision New Portal Copy</h3>
              <button onClick={() => setShowProvision(false)} className="text-slate-400 hover:text-slate-600"><i className="fa-solid fa-xmark" /></button>
            </div>
            <div className="p-5 space-y-3">
              <p className="text-[12px] text-slate-500 bg-blue-50 border border-blue-100 rounded-lg p-2.5">Creates a fully isolated portal copy: its own SQLite database, admin login, branding and subdomain slug.</p>
              <div className="grid grid-cols-2 gap-3">
                <label className="col-span-2 block"><span className="text-[13px] font-semibold text-slate-700">Company Name *</span>
                  <input value={form.company_name} onChange={(e) => set('company_name', e.target.value)} className="form-input w-full mt-1" placeholder="e.g. New OEP Pvt Ltd" /></label>
                <label className="block"><span className="text-[13px] font-semibold text-slate-700">Short Name</span>
                  <input value={form.short_name} onChange={(e) => set('short_name', e.target.value)} className="form-input w-full mt-1" placeholder="Short name" /></label>
                <label className="block"><span className="text-[13px] font-semibold text-slate-700">Brand Color</span>
                  <input type="color" value={form.primary_color} onChange={(e) => set('primary_color', e.target.value)} className="form-input w-full mt-1 h-[38px] p-1" /></label>
                <label className="col-span-2 block"><span className="text-[13px] font-semibold text-slate-700">Subtitle</span>
                  <input value={form.subtitle} onChange={(e) => set('subtitle', e.target.value)} className="form-input w-full mt-1" /></label>
              </div>
              <div className="border-t border-slate-100 pt-3">
                <p className="text-[13px] font-bold text-slate-700 mb-2">Portal Administrator</p>
                <div className="grid grid-cols-2 gap-3">
                  <label className="block"><span className="text-[13px] font-semibold text-slate-700">Admin Name *</span>
                    <input value={form.admin_name} onChange={(e) => set('admin_name', e.target.value)} className="form-input w-full mt-1" /></label>
                  <label className="block"><span className="text-[13px] font-semibold text-slate-700">Phone</span>
                    <input value={form.contact_phone} onChange={(e) => set('contact_phone', e.target.value)} className="form-input w-full mt-1" /></label>
                  <label className="block"><span className="text-[13px] font-semibold text-slate-700">Admin Email *</span>
                    <input type="email" value={form.admin_email} onChange={(e) => set('admin_email', e.target.value)} className="form-input w-full mt-1" /></label>
                  <label className="block"><span className="text-[13px] font-semibold text-slate-700">Admin Password *</span>
                    <input type="password" value={form.admin_password} onChange={(e) => set('admin_password', e.target.value)} className="form-input w-full mt-1" placeholder="min 6 chars" /></label>
                </div>
              </div>
              <label className="block"><span className="text-[13px] font-semibold text-slate-700">Notes</span>
                <textarea value={form.notes} onChange={(e) => set('notes', e.target.value)} rows={2} className="form-input w-full mt-1" /></label>
              {/* Sticky action bar. This form is taller than a 640px-high
                  viewport, so with a statically-positioned footer the primary
                  action sat below the fold and looked absent until the user
                  guessed to scroll INSIDE the dialog. Sticky keeps it on screen
                  at every height, matching the role dialog and <Modal>. */}
              <div className="flex gap-2 pt-3 pb-1 sticky bottom-0 bg-white border-t border-slate-100 -mx-5 px-5 mt-2">
                <button onClick={provision} disabled={saving} className="btn btn-primary flex-1">{saving ? <i className="fa-solid fa-spinner fa-spin" /> : <><i className="fa-solid fa-rocket mr-1.5" />Provision Portal</>}</button>
                <button onClick={() => setShowProvision(false)} disabled={saving} className="btn btn-secondary">Cancel</button>
              </div>
            </div>
          </div>
        </div>,
        document.body,
      )}

      {/* Tenant branding & custom-domain drawer */}
      {customizing && (
        <TenantBranding portal={customizing} onClose={() => setCustomizing(null)} onSaved={() => { setCustomizing(null); load() }} />
      )}
    </div>
  )
}

// ─────────────────── Tenant Branding & Custom Domain drawer ───────────────────
function TenantBranding({ portal, onClose, onSaved }: { portal: any; onClose: () => void; onSaved: () => void }) {
  const toast = useToast()
  const [subtitle, setSubtitle] = useState(portal.subtitle || '')
  const [color, setColor] = useState(portal.primary_color || '#2563eb')
  const [shortName, setShortName] = useState(portal.short_name || '')
  const [domain, setDomain] = useState(portal.custom_domain || '')
  const [logoUrl, setLogoUrl] = useState(portal.logo_url || '')
  const [officeName, setOfficeName] = useState(portal.office_name || '')
  const [licenseNumber, setLicenseNumber] = useState(portal.license_number || '')
  const [officeAddress, setOfficeAddress] = useState(portal.office_address || '')
  // The typeface for this office's shell AND its printed documents. The column,
  // the migration, the boot payload, the validated API field and the CSS
  // variable were all built and proven -- but nothing in the UI ever sent a
  // value, so the only way an operator could set an office's font was to issue
  // a PUT by hand. A setting that exists everywhere except where the operator
  // can reach it is not a delivered feature.
  const [fontKey, setFontKey] = useState(portal.font_key || 'system')
  const [savingMeta, setSavingMeta] = useState(false)
  const [savingLetterhead, setSavingLetterhead] = useState(false)
  const [savingDomain, setSavingDomain] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadPct, setUploadPct] = useState(0)
  // This drawer is only ever rendered when open (the parent gates on
  // `customizing`), so `open` is a literal true. Escape is disabled while any
  // save or upload is in flight -- a half-uploaded letterhead is worse than a
  // drawer that stays put for a second.
  const drawerRef = useDialogKeys<HTMLElement>(
    true, onClose,
    { closeOnEscape: !(savingMeta || savingLetterhead || savingDomain || uploading) },
  )

  const saveBranding = async () => {
    setSavingMeta(true)
    try {
      await api.put(`/tenants/${portal.id}`, { subtitle, primary_color: color, short_name: shortName })
      // The typeface goes through PUT /branding, not PUT /tenants/{id}: only
      // BrandingUpdate carries the font_key validator that refuses a key the
      // renderer cannot resolve. Posting it to the looser endpoint would let an
      // unknown value reach the column, and that column is interpolated into a
      // live CSS custom property and into the document stylesheet.
      await api.put(`/tenants/${portal.id}/branding`, { font_key: fontKey })
      toast.show('Branding saved', 'success')
      onSaved()
    } catch (e) { toast.show(apiError(e, 'Failed to save branding'), 'error') } finally { setSavingMeta(false) }
  }

  const saveLetterhead = async () => {
    setSavingLetterhead(true)
    try {
      await api.put(`/tenants/${portal.id}/branding`, {
        office_name: officeName,
        license_number: licenseNumber,
        office_address: officeAddress,
        // Deliberately NOT font_key. It is owned by the Brand & Identity
        // section's save above. Sending it from two buttons means whichever
        // was pressed last wins, so an operator who edits the font, then
        // edits the address, silently reverts the font to whatever this
        // drawer was opened with.
      })
      toast.show('Letterhead / office identity saved', 'success')
      onSaved()
    } catch (e) { toast.show(apiError(e, 'Failed to save letterhead'), 'error') } finally { setSavingLetterhead(false) }
  }

  const tenantUrl = `/t/${portal.slug}/`

  const uploadLogo = async (file: File | null) => {
    if (!file) return
    if (!/^image\/(png|jpe?g|webp|svg\+xml)$/.test(file.type)) { toast.show('Logo must be PNG, JPG, WEBP or SVG', 'error'); return }
    if (file.size > 3 * 1024 * 1024) { toast.show('Logo must be under 3 MB', 'error'); return }
    setUploading(true); setUploadPct(1)
    try {
      const fd = new FormData(); fd.append('file', file)
      const { data: t } = await api.post(`/tenants/${portal.id}/logo`, fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
        onUploadProgress: (ev) => {
          if (ev.total) setUploadPct(Math.round((ev.loaded / ev.total) * 100))
        },
      })
      const nextLogo = t.logo_url ? `${t.logo_url}?t=${Date.now()}` : ''
      setLogoUrl(nextLogo)
      toast.show(`Logo uploaded for ${portal.company_name}. Open Tenant shows this branding.`, 'success')
    } catch (e) { toast.show(apiError(e, 'Logo upload failed'), 'error') } finally { setUploading(false); setUploadPct(0) }
  }

  const saveDomain = async (unbind = false) => {
    setSavingDomain(true)
    try {
      const { data: t } = await api.put(`/tenants/${portal.id}/domain`, { custom_domain: unbind ? null : domain.trim() })
      setDomain(t.custom_domain || '')
      toast.show(unbind ? 'Custom domain removed' : `Domain bound: ${t.custom_domain}`, 'success')
    } catch (e) { toast.show(apiError(e, 'Failed to update domain'), 'error') } finally { setSavingDomain(false) }
  }

  return createPortal(
    <div className="portal-branding-overlay fixed inset-0 z-50 flex justify-end bg-black/40" onClick={onClose}>
      <aside ref={drawerRef} className="portal-branding-drawer bg-white w-full max-w-md h-full overflow-y-auto shadow-2xl" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label={`Customize branding for ${portal.company_name}`}>
        <div className="px-4 sm:px-5 py-3 sm:py-4 border-b border-slate-100 flex items-center justify-between sticky top-0 bg-white z-10">
          <h3 className="font-bold text-slate-900 min-w-0 truncate"><i className="fa-solid fa-palette text-blue-600 mr-2" />Customize Tenant — {portal.company_name}</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600 min-w-[44px] min-h-[44px]" aria-label="Close"><i className="fa-solid fa-xmark text-lg" /></button>
        </div>

        <div className="p-4 sm:p-5 space-y-5 sm:space-y-6">
          <p className="text-[12px] text-slate-600 bg-blue-50 border border-blue-100 rounded-lg p-2.5">
            This changes only the tenant copy <strong>{portal.company_name}</strong>. The root Dogar portal is not overwritten. Use <a href={tenantUrl} target="_blank" rel="noopener noreferrer" className="font-semibold text-blue-700 hover:underline">Open Tenant</a> to preview the tenant-branded portal.
          </p>
          {/* Logo */}
          <section>
            <h4 className="text-[13px] font-bold text-slate-700 uppercase mb-2"><i className="fa-solid fa-image mr-1.5 text-slate-400" />Tenant Company Logo</h4>
            <div className="max-w-[160px]">
              <ImageUpload
                value={logoUrl}
                onChange={uploadLogo}
                uploading={uploading}
                progress={uploadPct}
                fit="contain"
                aspect="1 / 1"
                maxSizeMb={3}
                acceptRe={/^image\/(png|jpe?g|webp|svg\+xml)$/}
                accept="image/png,image/jpeg,image/webp,image/svg+xml"
                hint="PNG, JPG, WEBP or SVG · max 3 MB."
              />
            </div>
            <p className="text-[11px] text-slate-400 mt-1.5">Shows on this tenant's login, sidebar &amp; printed letterheads.</p>
          </section>

          {/* Branding */}
          <section className="space-y-3">
            <h4 className="text-[13px] font-bold text-slate-700 uppercase"><i className="fa-solid fa-swatchbook mr-1.5 text-slate-400" />Brand &amp; Identity</h4>
            <label className="block"><span className="text-[13px] font-semibold text-slate-700">Short Name</span>
              <input value={shortName} onChange={(e) => setShortName(e.target.value)} className="form-input w-full mt-1" placeholder="e.g. DTC" /></label>
            <label className="block"><span className="text-[13px] font-semibold text-slate-700">Subtitle / Tagline</span>
              <input value={subtitle} onChange={(e) => setSubtitle(e.target.value)} className="form-input w-full mt-1" placeholder="Overseas Employment Promoters" /></label>
            <label className="block"><span className="text-[13px] font-semibold text-slate-700">Primary Brand Color</span>
              <div className="flex items-center gap-2 mt-1">
                <input type="color" value={color} onChange={(e) => setColor(e.target.value)} className="form-input w-14 h-[38px] p-1" aria-label="Brand color picker" />
                <input value={color} onChange={(e) => setColor(e.target.value)} className="form-input flex-1 font-mono text-[13px]" placeholder="#2563eb" />
              </div>
            </label>
            {/* Typeface. Options come from FONT_STACKS in lib/branding.ts, never
                from a list written out here: a hand-written <option> set would
                be a FOURTH copy of the allow-list (Python contract, TS stacks,
                API validator) and the one most likely to drift, offering the
                operator a font the API then rejects with a 422. */}
            <label className="block"><span className="text-[13px] font-semibold text-slate-700">Document &amp; Interface Typeface</span>
              <select id="tenant-font-key" value={fontKey} onChange={(e) => setFontKey(e.target.value)}
                      className="form-input w-full mt-1" style={{ fontFamily: resolveFontStack(fontKey) }}>
                {Object.keys(FONT_STACKS).map((k) => (
                  <option key={k} value={k} style={{ fontFamily: FONT_STACKS[k] }}>
                    {k === 'system' ? 'System default (Inter)' : k.charAt(0).toUpperCase() + k.slice(1)}
                  </option>
                ))}
              </select>
              <span className="block text-[12px] text-slate-500 mt-1">
                Applies to this office's portal shell and to the letters, undertakings and demand
                files it prints. Dogar Trading's own documents are unaffected.
              </span>
            </label>
            <button onClick={saveBranding} disabled={savingMeta} className="btn btn-primary w-full">
              {savingMeta ? <i className="fa-solid fa-spinner fa-spin mr-1.5" /> : <i className="fa-solid fa-floppy-disk mr-1.5" />}Save Branding
            </button>
          </section>

          {/* Letterhead / Office identity — printed on this office's documents */}
          <section className="space-y-3 border-t border-slate-100 pt-5">
            <h4 className="text-[13px] font-bold text-slate-700 uppercase"><i className="fa-solid fa-file-signature mr-1.5 text-slate-400" />Letterhead &amp; Office Identity</h4>
            <p className="text-[12px] text-slate-500 bg-amber-50 border border-amber-100 rounded-lg p-2.5">
              These appear on this recruiting office's printed letterheads, receipts &amp; demand files — its own name, licence number and address (not Dogar Trading's).
            </p>
            <label className="block"><span className="text-[13px] font-semibold text-slate-700">Recruiting Office Name</span>
              <input value={officeName} onChange={(e) => setOfficeName(e.target.value)} className="form-input w-full mt-1" placeholder="e.g. Beta Overseas Recruitment Office" /></label>
            <label className="block"><span className="text-[13px] font-semibold text-slate-700">OEP Licence Number</span>
              <input value={licenseNumber} onChange={(e) => setLicenseNumber(e.target.value)} className="form-input w-full mt-1 font-mono text-[13px]" placeholder="e.g. OEP-LHR-4521" /></label>
            <label className="block"><span className="text-[13px] font-semibold text-slate-700">Office Address</span>
              <textarea value={officeAddress} onChange={(e) => setOfficeAddress(e.target.value)} rows={2} className="form-input w-full mt-1" placeholder="Full postal address for the letterhead footer" /></label>
            <button onClick={saveLetterhead} disabled={savingLetterhead} className="btn btn-primary w-full">
              {savingLetterhead ? <i className="fa-solid fa-spinner fa-spin mr-1.5" /> : <i className="fa-solid fa-file-signature mr-1.5" />}Save Letterhead
            </button>
          </section>

          {/* Custom domain */}
          <section className="space-y-3 border-t border-slate-100 pt-5">
            <h4 className="text-[13px] font-bold text-slate-700 uppercase"><i className="fa-solid fa-globe mr-1.5 text-slate-400" />Custom Domain</h4>
            <p className="text-[12px] text-slate-500 bg-blue-50 border border-blue-100 rounded-lg p-2.5">
              Bind a dedicated domain (e.g. <span className="font-mono">portal.mytravel.pk</span>). Point a <span className="font-mono">CNAME</span> at the platform host, then bind it here. Only one domain per portal.
            </p>
            <div className="flex flex-col sm:flex-row gap-2">
              <input value={domain} onChange={(e) => setDomain(e.target.value)} className="form-input flex-1 min-w-0 font-mono text-[13px]" placeholder="portal.example.pk" />
              <button onClick={() => saveDomain(false)} disabled={savingDomain || !domain.trim()} className="btn btn-primary shrink-0">
                {savingDomain ? <i className="fa-solid fa-spinner fa-spin" /> : 'Bind'}
              </button>
            </div>
            {portal.custom_domain && (
              <button onClick={() => saveDomain(true)} disabled={savingDomain} className="text-[13px] font-semibold text-rose-600 hover:text-rose-800">
                <i className="fa-solid fa-link-slash mr-1" />Remove custom domain ({portal.custom_domain})
              </button>
            )}
          </section>
        </div>
      </aside>
    </div>,
    document.body,
  )
}
