/**
 * Content Tab — Rich-text WYSIWYG editor for letter-style documents.
 * Uses contenteditable with execCommand for WYSIWYG formatting (no heavy
 * third-party editor dependency). Provides:
 *   - Formatting toolbar (Bold, Italic, Underline, Strikethrough, lists, alignment)
 *   - Field palette sidebar with {{key}} merge-tag insertion
 *   - Page size dropdown
 *   - Add page button
 *   - Insert Columns / Trade table buttons
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { Palette } from './types'
import { iconForType, token } from './types'
import { sanitizeHtml } from '../../lib/sanitizeHtml'

interface Props {
  value: string
  onChange: (v: string) => void
  palette: Palette
  pageSize: string
  onPageSizeChange: (v: string) => void
  onAddPage: () => void
}

export default function ContentTab({ value, onChange, palette, pageSize, onPageSizeChange, onAddPage }: Props) {
  const editorRef = useRef<HTMLDivElement>(null)
  const [paletteSearch, setPaletteSearch] = useState('')
  const [paletteExpanded, setPaletteExpanded] = useState<Record<string, boolean>>({})
  const initialized = useRef(false)

  // Set initial HTML content once. Stored content is sanitized before being
  // written into the live DOM so a persisted XSS payload cannot execute when
  // another user opens the template (M-audit 2026-07-24).
  useEffect(() => {
    if (editorRef.current && !initialized.current) {
      editorRef.current.innerHTML = value
        ? sanitizeHtml(value)
        : '<p>Type the document body here…</p>'
      initialized.current = true
    }
  }, [value])

  // Sanitize on the way out too, so nothing dangerous is ever persisted.
  const handleInput = useCallback(() => {
    if (editorRef.current) {
      onChange(sanitizeHtml(editorRef.current.innerHTML))
    }
  }, [onChange])

  const exec = (cmd: string, val?: string) => {
    document.execCommand(cmd, false, val)
    editorRef.current?.focus()
    handleInput()
  }

  const insertMergeField = (key: string) => {
    editorRef.current?.focus()
    const tag = `{{${key}}}`
    document.execCommand('insertText', false, tag)
    handleInput()
  }

  const insertColumns = () => {
    editorRef.current?.focus()
    const html = '<table style="width:100%;border-collapse:collapse;"><tr><td style="width:50%;vertical-align:top;padding:4px;border:1px dashed #ccc;">Column 1</td><td style="width:50%;vertical-align:top;padding:4px;border:1px dashed #ccc;">Column 2</td></tr></table><p></p>'
    document.execCommand('insertHTML', false, html)
    handleInput()
  }

  const insertTradeTable = () => {
    editorRef.current?.focus()
    const html = '<table style="width:100%;border-collapse:collapse;border:1px solid #666;margin:8px 0;"><thead><tr style="background:#e5e7eb;"><th style="padding:4px 8px;border:1px solid #666;text-align:left;">S#</th><th style="padding:4px 8px;border:1px solid #666;text-align:left;">Trade</th><th style="padding:4px 8px;border:1px solid #666;text-align:center;">Qty</th><th style="padding:4px 8px;border:1px solid #666;text-align:center;">Assigned</th><th style="padding:4px 8px;border:1px solid #666;text-align:center;">Available</th></tr></thead><tbody><tr><td style="padding:4px 8px;border:1px solid #666;">{{trades_table}}</td><td style="padding:4px 8px;border:1px solid #666;"></td><td style="padding:4px 8px;border:1px solid #666;text-align:center;"></td><td style="padding:4px 8px;border:1px solid #666;text-align:center;"></td><td style="padding:4px 8px;border:1px solid #666;text-align:center;"></td></tr></tbody></table><p></p>'
    document.execCommand('insertHTML', false, html)
    handleInput()
  }

  // Palette search filtering
  const q = paletteSearch.trim().toLowerCase()
  const filteredGroups = useMemo(() => {
    return (palette.groups || []).map((g) => ({
      ...g,
      fields: q
        ? g.fields.filter((f) => f.label.toLowerCase().includes(q) || f.key.toLowerCase().includes(q))
        : g.fields,
    })).filter((g) => g.fields.length > 0)
  }, [palette, q])

  const toggleGroup = (group: string) => {
    setPaletteExpanded((prev) => ({ ...prev, [group]: !prev[group] }))
  }

  const ToolBtn = ({ cmd, icon, label, active }: { cmd: string; icon: string; label: string; active?: boolean }) => (
    <button
      type="button"
      title={label}
      onMouseDown={(e) => { e.preventDefault(); exec(cmd) }}
      className={`w-7 h-7 flex items-center justify-center text-[12px] rounded transition ${active ? 'bg-blue-600 text-white' : 'text-slate-600 hover:bg-slate-100'}`}
    >
      <i className={icon} />
    </button>
  )

  return (
    <div className="grid grid-cols-12 gap-3">
      {/* Left: Field palette for merge tags */}
      <aside className="col-span-12 lg:col-span-3">
        <div className="bg-white border border-slate-200 rounded-xl shadow-sm" style={{ maxHeight: 'calc(100vh - 320px)', overflowY: 'auto' }}>
          <div className="px-3 py-2 border-b border-slate-100">
            <h3 className="text-[11px] font-bold text-slate-500 uppercase tracking-wide">Merge Fields</h3>
          </div>
          <div className="p-2">
            <input
              type="text"
              value={paletteSearch}
              onChange={(e) => setPaletteSearch(e.target.value)}
              placeholder="Search fields…"
              className="w-full px-2.5 py-1.5 text-xs border border-slate-300 rounded-md focus:ring-2 focus:ring-blue-100 focus:border-blue-500 outline-none mb-2"
            />
            {/* Action buttons */}
            <div className="grid grid-cols-2 gap-1 mb-2">
              <button type="button" onClick={insertColumns} className="dsg-insert-btn text-center" title="Insert 2-column layout">
                <i className="fa-solid fa-columns mr-1" /> Columns
              </button>
              <button type="button" onClick={insertTradeTable} className="dsg-insert-btn text-center" title="Insert trades table placeholder">
                <i className="fa-solid fa-table mr-1" /> Trade table
              </button>
            </div>
          </div>
          <div className="px-1 pb-2">
            {filteredGroups.map((g) => {
              const expanded = paletteExpanded[g.group] !== false
              return (
                <div key={g.group} className="mb-0.5">
                  <button
                    type="button"
                    onClick={() => toggleGroup(g.group)}
                    className="w-full flex items-center justify-between px-2 py-1.5 text-[11px] font-semibold text-slate-600 hover:bg-slate-50 rounded"
                  >
                    <span>{g.group} <span className="font-normal text-slate-400">({g.fields.length})</span></span>
                    <i className={`fa-solid fa-chevron-${expanded ? 'up' : 'down'} text-[9px] text-slate-400`} />
                  </button>
                  {expanded && (
                    <div className="pl-1">
                      {g.fields.map((field) => (
                        <button
                          key={`${g.group}:${field.key}`}
                          type="button"
                          onClick={() => insertMergeField(field.key)}
                          className="w-full text-left px-2 py-1 text-[11px] hover:bg-blue-50 rounded flex flex-col gap-0"
                          title={`Insert ${token(field.key)}`}
                        >
                          <span className="text-slate-700 truncate flex items-center gap-1">
                            <i className={`${iconForType(field.field_type)} text-[10px] text-slate-400`} />
                            {field.label}
                          </span>
                          <span className="text-[9px] font-mono text-slate-400 truncate">{token(field.key)}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      </aside>

      {/* Center: WYSIWYG Editor */}
      <section className="col-span-12 lg:col-span-9">
        <div className="bg-white border border-slate-200 rounded-xl shadow-sm">
          {/* Toolbar */}
          <div className="px-3 py-2 border-b border-slate-100 flex items-center gap-1 flex-wrap">
            <ToolBtn cmd="bold" icon="fa-solid fa-bold" label="Bold" />
            <ToolBtn cmd="italic" icon="fa-solid fa-italic" label="Italic" />
            <ToolBtn cmd="underline" icon="fa-solid fa-underline" label="Underline" />
            <ToolBtn cmd="strikeThrough" icon="fa-solid fa-strikethrough" label="Strikethrough" />
            <span className="w-px h-5 bg-slate-200 mx-1" />
            <ToolBtn cmd="justifyLeft" icon="fa-solid fa-align-left" label="Align left" />
            <ToolBtn cmd="justifyCenter" icon="fa-solid fa-align-center" label="Align center" />
            <ToolBtn cmd="justifyRight" icon="fa-solid fa-align-right" label="Align right" />
            <ToolBtn cmd="justifyFull" icon="fa-solid fa-align-justify" label="Justify" />
            <span className="w-px h-5 bg-slate-200 mx-1" />
            <ToolBtn cmd="insertUnorderedList" icon="fa-solid fa-list-ul" label="Bullet list" />
            <ToolBtn cmd="insertOrderedList" icon="fa-solid fa-list-ol" label="Numbered list" />
            <span className="w-px h-5 bg-slate-200 mx-1" />
            <select
              title="Font size"
              onChange={(e) => { if (e.target.value) exec('fontSize', e.target.value) }}
              className="px-2 py-1 text-xs border border-slate-300 rounded-md bg-white"
              defaultValue=""
            >
              <option value="" disabled>Size</option>
              <option value="1">8pt</option>
              <option value="2">10pt</option>
              <option value="3">12pt</option>
              <option value="4">14pt</option>
              <option value="5">18pt</option>
              <option value="6">24pt</option>
              <option value="7">36pt</option>
            </select>
            <select
              title="Block format"
              onChange={(e) => { if (e.target.value) exec('formatBlock', e.target.value) }}
              className="px-2 py-1 text-xs border border-slate-300 rounded-md bg-white"
              defaultValue=""
            >
              <option value="" disabled>Format</option>
              <option value="p">Paragraph</option>
              <option value="h1">Heading 1</option>
              <option value="h2">Heading 2</option>
              <option value="h3">Heading 3</option>
              <option value="blockquote">Quote</option>
            </select>
            <span className="w-px h-5 bg-slate-200 mx-1" />
            {/* Page controls */}
            <select
              value={pageSize}
              onChange={(e) => onPageSizeChange(e.target.value)}
              className="px-2 py-1 text-xs border border-slate-300 rounded-md bg-white ml-auto"
              title="Page size"
            >
              <option value="a4">A4</option>
              <option value="letter">Letter</option>
              <option value="legal">Legal</option>
            </select>
            <button
              type="button"
              onClick={onAddPage}
              className="px-2 py-1 text-xs font-semibold text-slate-500 hover:text-blue-600 rounded-md border border-slate-300 bg-white hover:bg-slate-50"
              title="Add page"
            >
              <i className="fa-solid fa-plus mr-1" /> Add page
            </button>
          </div>

          {/* Editor area */}
          <div
            ref={editorRef}
            contentEditable
            suppressContentEditableWarning
            onInput={handleInput}
            className="px-8 py-6 min-h-[500px] text-sm leading-relaxed outline-none focus:ring-0"
            style={{
              fontFamily: 'Arial, Helvetica, sans-serif',
              maxWidth: 700,
              margin: '0 auto',
            }}
            data-placeholder="Type the document body here… Use merge fields from the left panel to insert {{placeholders}}."
          />
          <div className="px-3 py-1.5 border-t border-slate-100 text-[10px] text-slate-400">
            Insert merge fields from the left panel. They will be replaced with real data at print time.
          </div>
        </div>
      </section>
    </div>
  )
}
