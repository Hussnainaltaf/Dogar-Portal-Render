import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, apiError } from '@/lib/api'
import { useToast } from '@/components/Toast'
import { printInFrame } from '@/lib/print'
import { humanize, prettifyFileName } from '@/lib/format'
import { useLiveStatus } from '@/lib/useLiveStatus'

/* Full v2-parity Documents page — pinned System Templates grid +
 * filterable templates table (category filter + search) with
 * Configure-positions / Print-preview / Delete actions. */

interface Template {
  id: number
  name: string
  description?: string
  category?: string
  data_source?: string
  background_image?: string
  is_active?: boolean
  fields?: unknown[]
}

const CATEGORY_ICON: Record<string, string> = {
  'Protector Documents': 'fa-shield-halved',
  protector_documents: 'fa-shield-halved',
  'Permission Documents': 'fa-stamp',
  permission_documents: 'fa-stamp',
  'Visa Process': 'fa-passport',
  visa_process: 'fa-passport',
  custom: 'fa-file-pen',
}

export function Documents() {
  const toast = useToast()
  const [templates, setTemplates] = useState<Template[]>([])
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')
  const [cat, setCat] = useState('')

  const load = () => {
    setLoading(true)
    api.get('/documents/templates')
      .then(({ data }) => setTemplates(data.items || data || []))
      .catch((e) => toast.show(apiError(e), 'error'))
      .finally(() => setLoading(false))
  }
  useEffect(load, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Live status: refresh templates when document status changes on the server
  // (SSE with polling fallback).
  const loadRef = useRef(load)
  loadRef.current = load
  useLiveStatus(useCallback(() => { loadRef.current() }, []))

  const categories = useMemo(
    () => [...new Set(templates.map((t) => t.category).filter(Boolean))].sort() as string[],
    [templates],
  )

  // System templates = anything not custom, or custom with a background image.
  const systemTemplates = useMemo(
    () => templates.filter((t) => t.category !== 'custom' || !!t.background_image),
    [templates],
  )

  const filtered = useMemo(() => {
    let items = templates
    if (cat) items = items.filter((t) => t.category === cat)
    const q = query.trim().toLowerCase()
    if (q) items = items.filter((t) => [t.name, t.description, t.category].some((v) => (v || '').toLowerCase().includes(q)))
    return items
  }, [templates, cat, query])

  const printTemplate = (id: number) => printInFrame(`/api/documents/templates/${id}/print`)

  const deleteTemplate = async (id: number) => {
    if (!confirm('Delete this template?')) return
    try { await api.delete(`/documents/templates/${id}`); toast.show('Template deleted', 'success'); load() }
    catch (e) { toast.show(apiError(e), 'error') }
  }

  return (
    <div className="space-y-5">
      <header className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-[22px] font-bold text-slate-800 flex items-center gap-2">
          <i className="fa-regular fa-file-pdf text-blue-600" /> Documents
        </h1>
      </header>

      {/* Pinned System Templates */}
      <section className="card overflow-hidden">
        <div className="card-header"><h3 className="font-semibold text-slate-800 text-sm"><i className="fa-solid fa-thumbtack text-blue-600 mr-1.5" /> System Templates</h3></div>
        <div className="p-4">
          {loading ? <div className="text-center py-6 text-slate-400"><i className="fa-solid fa-spinner fa-spin text-blue-600" /></div>
          : systemTemplates.length === 0 ? <div className="text-center py-6 text-slate-400">No system templates</div>
          : <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
              {systemTemplates.map((t) => (
                <Link key={t.id} to={`/documents/customize/${t.id}`}
                  className="border border-slate-200 rounded-lg p-3 hover:border-blue-400 hover:shadow-sm transition flex items-start gap-2.5 group">
                  <span className="w-9 h-9 rounded-lg bg-blue-50 text-blue-600 flex items-center justify-center shrink-0">
                    <i className={`fa-solid ${CATEGORY_ICON[t.category || ''] || 'fa-file-lines'}`} />
                  </span>
                  <div className="min-w-0">
                    <div className="font-semibold text-[13px] text-slate-800 truncate group-hover:text-blue-600">{t.name}</div>
                    <div className="text-[11px] text-slate-400 truncate">{humanize(t.category) || '—'}</div>
                    {t.background_image && <div className="text-[10px] text-emerald-600 mt-0.5"><i className="fa-solid fa-image mr-1" />background</div>}
                  </div>
                </Link>
              ))}
            </div>}
        </div>
      </section>

      {/* All templates table */}
      <section className="card overflow-hidden">
        <div className="card-header flex items-center justify-between flex-wrap gap-3">
          <div className="relative">
            <i className="fa-solid fa-search absolute left-3 top-2.5 text-slate-400 text-sm" />
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search templates…"
              className="pl-9 pr-3 py-2 text-sm border border-slate-200 rounded-md w-72 focus:outline-none focus:border-blue-500" />
          </div>
          <select value={cat} onChange={(e) => setCat(e.target.value)} className="px-3 py-2 text-sm border border-slate-200 rounded-md focus:outline-none focus:border-blue-500">
            <option value="">All Categories</option>
            {categories.map((c) => <option key={c} value={c}>{humanize(c)}</option>)}
          </select>
        </div>
        <div className="table-scroll">
          <table className="data-table responsive-cards w-full">
            <thead>
              <tr><th>Name</th><th>Category</th><th>Data Source</th><th>Background</th><th>Fields</th><th className="text-right">Actions</th></tr>
            </thead>
            <tbody>
              {loading ? <tr><td colSpan={6} className="text-center py-10 text-slate-400"><i className="fa-solid fa-spinner fa-spin text-blue-600" /></td></tr>
              : filtered.length === 0 ? <tr><td colSpan={6} className="text-center py-10 text-slate-400">No templates match</td></tr>
              : filtered.map((t) => (
                <tr key={t.id}>
                  <td data-label="Name" data-primary className="font-medium text-slate-800">{t.name}<div className="text-[11px] text-slate-400 font-normal">{t.description}</div></td>
                  <td data-label="Category"><span className="px-2 py-0.5 rounded text-xs bg-slate-100 text-slate-700">{humanize(t.category) || '—'}</span></td>
                  <td data-label="Data Source" className="text-slate-600">{humanize(t.data_source) || '—'}</td>
                  <td data-label="Background">{t.background_image
                    ? <span className="text-emerald-600 text-xs" title={t.background_image.split('/').pop()}><i className="fa-solid fa-check mr-1" />{prettifyFileName(t.background_image)}</span>
                    : <span className="text-slate-400 text-xs">—</span>}</td>
                  <td data-label="Fields" className="text-slate-600">{(t.fields || []).length}</td>
                  <td data-label="Actions" className="text-right whitespace-nowrap">
                    <Link to={`/documents/customize/${t.id}`} title="Configure field positions" className="text-blue-600 hover:text-blue-700 px-2"><i className="fa-regular fa-pen-to-square" /></Link>
                    <button onClick={() => printTemplate(t.id)} title="Preview print (data overlay)" className="text-blue-600 hover:text-blue-700 px-2"><i className="fa-solid fa-print" /></button>
                    <button onClick={() => deleteTemplate(t.id)} title="Delete" className="text-rose-600 hover:text-rose-700 px-2"><i className="fa-regular fa-trash-can" /></button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}

export default Documents
