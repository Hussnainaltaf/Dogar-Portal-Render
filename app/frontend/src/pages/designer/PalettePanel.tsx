/**
 * Designer left column — grouped field palette.
 * Palette panel: search, expand-all, collapsible groups
 * (first three open by default), insert primitives, custom text, per-field
 * drag / double-click / touch insertion, "placed" indicators.
 */
import { useMemo, useState } from 'react'
import type { Palette, PaletteField } from './types'
import { iconForType, token } from './types'

export interface PaletteDragPayload {
  key: string
  label: string
  ftype: string
  staticValue?: string
  primitive?: 'text' | 'checkbox' | 'barcode' | 'trade_table'
}

const PRIMITIVES: Array<{
  kind: NonNullable<PaletteDragPayload['primitive']>
  label: string
  icon: string
  title: string
  payload: PaletteDragPayload
}> = [
  {
    kind: 'text',
    label: 'Text box',
    icon: 'fa-regular fa-square',
    title: 'Insert or drag a free-form text box',
    payload: { key: '__static__', label: 'Text', ftype: 'static', staticValue: 'New text', primitive: 'text' },
  },
  {
    kind: 'checkbox',
    label: 'Checkbox',
    icon: 'fa-regular fa-square-check',
    title: 'Insert or drag a checkbox',
    payload: { key: '__static_checkbox__', label: 'Checkbox', ftype: 'checkbox', primitive: 'checkbox' },
  },
  {
    kind: 'barcode',
    label: 'Barcode',
    icon: 'fa-solid fa-barcode',
    title: 'Insert or drag a barcode',
    payload: { key: '__static_barcode__', label: 'Barcode', ftype: 'barcode', primitive: 'barcode' },
  },
  {
    kind: 'trade_table',
    label: 'Trade table',
    icon: 'fa-solid fa-table',
    title: 'Insert or drag a trade table',
    payload: { key: 'trades_table', label: 'Trades Table', ftype: 'trade_table', primitive: 'trade_table' },
  },
]

interface Props {
  palette: Palette
  placedKeys: Set<string>
  onInsertPrimitive: (kind: 'text' | 'checkbox' | 'barcode' | 'trade_table') => void
  onAddCustomText: (text: string) => void
  onInsertField: (p: PaletteDragPayload) => void
  onTouchPick: (p: PaletteDragPayload | null) => void
}

export default function PalettePanel({ palette, placedKeys, onInsertPrimitive, onAddCustomText, onInsertField, onTouchPick }: Props) {
  const [query, setQuery] = useState('')
  const [customText, setCustomText] = useState('')
  // Collapsed state per group label; default: groups after the first three collapsed.
  const [openOverride, setOpenOverride] = useState<Record<string, boolean>>({})
  const [expandAll, setExpandAll] = useState(false)

  const q = query.trim().toLowerCase()
  const groups = useMemo(() => {
    const gs = palette.groups || []
    return gs
      .map((g) => ({
        group: g.group,
        fields: (g.fields || []).filter(
          (f) => !q || f.label.toLowerCase().includes(q) || f.key.toLowerCase().includes(q),
        ),
      }))
      .filter((g) => !q || g.fields.length > 0)
  }, [palette, q])

  const isOpen = (label: string, idx: number) => {
    if (q) return true
    if (label in openOverride) return openOverride[label]
    return expandAll || idx <= 2
  }

  const addCustom = () => {
    const v = customText.trim()
    if (!v) return
    onAddCustomText(v)
    setCustomText('')
  }

  const dragPayload = (e: React.DragEvent, payload: PaletteDragPayload) => {
    e.dataTransfer.setData('text/x-palette', JSON.stringify(payload))
    e.dataTransfer.effectAllowed = 'copy'
  }

  const dragStart = (e: React.DragEvent, f: PaletteField) => {
    dragPayload(e, { key: f.key, label: f.label, ftype: f.field_type })
  }

  return (
    <div className="bg-white border border-slate-200 rounded-xl shadow-sm flex flex-col" style={{ maxHeight: 'calc(100vh - 220px)', minHeight: 540 }}>
      {/* Search + header */}
      <div className="p-3 border-b border-slate-100">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-[11px] font-bold text-slate-500 uppercase tracking-wide">Field Palette</h3>
          <button
            type="button"
            onClick={() => { setExpandAll(true); setOpenOverride({}) }}
            className="text-[10px] text-blue-600 hover:underline"
          >Expand all</button>
        </div>
        <div className="relative">
          <i className="fa-solid fa-magnifying-glass absolute left-2.5 top-2 text-slate-400 text-xs" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            type="text"
            placeholder="Search fields..."
            className="w-full pl-7 pr-2 py-1.5 text-xs border border-slate-300 rounded-md focus:ring-2 focus:ring-blue-100 focus:border-blue-500 outline-none"
          />
        </div>
      </div>

      {/* Insert primitives */}
      <div className="px-3 py-2 border-b border-slate-100 bg-slate-50">
        <div className="text-[10px] font-bold text-slate-500 uppercase tracking-wide mb-1.5">Insert</div>
        <div className="grid grid-cols-2 gap-1.5">
          {PRIMITIVES.map((item) => (
            <button
              key={item.kind}
              type="button"
              draggable
              onClick={() => onInsertPrimitive(item.kind)}
              onDragStart={(e) => dragPayload(e, item.payload)}
              onTouchStart={() => onTouchPick(item.payload)}
              onTouchEnd={() => setTimeout(() => onTouchPick(null), 60)}
              title={item.title}
              aria-label={`${item.label}; click to insert or drag onto the page`}
              data-testid={`primitive-${item.kind}`}
              className="dsg-insert-btn cursor-grab active:cursor-grabbing"
            >
              <i className={`${item.icon} mr-1`} /> {item.label}
            </button>
          ))}
        </div>
        <div className="mt-2 flex gap-1.5">
          <input
            value={customText}
            onChange={(e) => setCustomText(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') addCustom() }}
            type="text"
            placeholder="Custom text (Enter to add)…"
            className="flex-1 px-2 py-1 text-xs border border-slate-300 rounded-md focus:ring-2 focus:ring-blue-100 focus:border-blue-500 outline-none"
          />
          <button type="button" onClick={addCustom} className="px-2 py-1 text-xs font-semibold text-white bg-blue-600 hover:bg-blue-700 rounded-md">Add</button>
        </div>
      </div>

      {/* Grouped field list */}
      <div className="flex-1 overflow-y-auto p-2 space-y-1" style={{ WebkitOverflowScrolling: 'touch', overscrollBehavior: 'contain' }}>
        {groups.length === 0 && (
          <div className="text-center text-xs text-slate-400 py-8">No fields available for this data source.</div>
        )}
        {groups.map((g, gi) => {
          const open = isOpen(g.group, gi)
          return (
            <div key={g.group} className="border border-slate-200 rounded-md bg-white overflow-hidden shadow-sm mb-2">
              <button
                type="button"
                onClick={() => setOpenOverride((prev) => ({ ...prev, [g.group]: !open }))}
                className="w-full text-left px-3 py-2 bg-slate-50 hover:bg-slate-100 flex items-center justify-between text-xs font-semibold text-slate-700 transition"
              >
                <span>{g.group} <span className="text-slate-400 font-normal ml-1">({g.fields.length})</span></span>
                <i className={`fa-solid fa-chevron-${open ? 'up' : 'down'} text-[10px] text-slate-400`} />
              </button>
              {open && (
                <div className="flex flex-col p-2 gap-1 bg-white border-t border-slate-100">
                  {g.fields.map((f) => {
                    const placed = placedKeys.has(f.key)
                    return (
                      <div
                        key={f.key}
                        draggable
                        onDragStart={(e) => dragStart(e, f)}
                        onDoubleClick={() => onInsertField({ key: f.key, label: f.label, ftype: f.field_type })}
                        onTouchStart={() => onTouchPick({ key: f.key, label: f.label, ftype: f.field_type })}
                        onTouchEnd={() => setTimeout(() => onTouchPick(null), 60)}
                        title={token(f.key)}
                        className={`flex items-start gap-2 px-2 py-1.5 text-xs text-slate-600 bg-white border border-slate-200 rounded cursor-grab hover:border-blue-400 hover:text-blue-700 hover:shadow-sm transition group select-none ${placed ? 'opacity-60 bg-slate-50' : ''}`}
                      >
                        <i className={`${iconForType(f.field_type)} w-4 text-center text-slate-400 group-hover:text-blue-500 transition mt-0.5`} />
                        <span className="flex-1 min-w-0 flex flex-col leading-tight">
                          <span className="truncate font-medium text-slate-700">{f.label}</span>
                          {(f.field_type === 'barcode' || f.field_type === 'photo' || f.field_type === 'trade_table')
                            ? <span className="truncate text-[10px] text-slate-400 italic">{f.field_type}</span>
                            : <span className="truncate text-[10px] font-mono text-slate-400">{token(f.key)}</span>}
                        </span>
                        {placed
                          ? <i className="fa-solid fa-check text-[10px] text-emerald-500 mt-0.5" />
                          : <i className="fa-solid fa-plus text-[10px] text-slate-300 group-hover:text-blue-500 mt-0.5 transition" />}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
