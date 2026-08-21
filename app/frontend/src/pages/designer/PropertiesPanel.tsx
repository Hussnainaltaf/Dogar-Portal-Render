/**
 * Designer right column — per-field property inspector.
 * Properties panel: identity (label / field key + quick
 * data-tag picker + merge-syntax badge), type, static value, geometry (pt),
 * typography (font family / size / color / align / page / B / I), and the
 * type-specific meta sections for char_cells, photo, barcode and trade_table.
 */
import { useState } from 'react'
import type { DesignerField, FieldMeta, Palette, TextTransform } from './types'
import { FIELD_TYPES, FONT_OPTIONS, cssFontStack, defaultMetaFor, token } from './types'
import { clampFieldBox, clampProp, refitCellMeta, round1 } from './engine'

interface Props {
  field: DesignerField | null
  palette: Palette
  pageW: number
  pageH: number
  onPatch: (patch: Partial<DesignerField>) => void
  onPatchMeta: (key: string, value: unknown) => void
  onDelete: () => void
  onDuplicate: () => void
}

const L = ({ children }: { children: React.ReactNode }) => (
  <span className="block text-[10px] text-slate-500">{children}</span>
)
const inputCls = 'w-full px-2 py-1 text-xs border border-slate-300 rounded-md focus:ring-2 focus:ring-blue-100 focus:border-blue-500 outline-none bg-white'

/** Numeric input: loose while typing, but commits LIVE on every parseable
 *  change so the spinner up/down arrows apply immediately (bug fix — before,
 *  arrow clicks only updated the box text and nothing applied until blur,
 *  making "font size up/down" appear broken). Intermediate states like "",
 *  "-", "1." stay as draft and are normalised on blur/Enter. */
function NumInput({ value, onCommit, step = 1, className = '' }: {
  value: number | string
  onCommit: (v: number) => void
  step?: number
  className?: string
}) {
  const [draft, setDraft] = useState<string | null>(null)
  const finish = (raw: string | null) => {
    if (raw !== null) onCommit(parseFloat(raw) || 0)
    setDraft(null)
  }
  return (
    <input
      type="number"
      step={step}
      className={`${inputCls} ${className}`}
      value={draft !== null ? draft : (Number.isFinite(Number(value)) ? String(value) : '')}
      onChange={(e) => {
        const raw = e.target.value
        setDraft(raw)
        // Live-apply as soon as the text parses to a finite number — this is
        // what makes the native spinner arrows (and typed digits) take effect
        // immediately on the canvas instead of waiting for blur.
        const n = parseFloat(raw)
        if (Number.isFinite(n) && String(n) === raw.trim()) onCommit(n)
      }}
      onBlur={() => finish(draft)}
      onKeyDown={(e) => {
        if (e.key === 'Enter') finish(draft)
      }}
    />
  )
}

export default function PropertiesPanel({ field: f, palette, pageW, pageH, onPatch, onPatchMeta, onDelete, onDuplicate }: Props) {
  const [copied, setCopied] = useState(false)

  if (!f) {
    return (
      <div className="p-6 text-center text-xs text-slate-400">
        <i className="fa-regular fa-hand-pointer text-3xl block mb-2 text-slate-300" />
        Select a field on the canvas to edit its properties
      </div>
    )
  }
  const meta: FieldMeta = f.meta || {}

  // Flatten palette to build the quick data-tag select (group → label).
  const suggestions = (palette.groups || [])
    .flatMap((g) => (g.fields || []).map((fld) => ({ label: `${g.group} → ${fld.label}`, key: fld.key })))
    .sort((a, b) => a.label.localeCompare(b.label))

  const commitNum = (prop: 'x' | 'y' | 'width' | 'height' | 'font_size' | 'page') => (v: number) => {
    if (['x', 'y', 'width', 'height'].includes(prop)) {
      const next = clampFieldBox({
        x: prop === 'x' ? v : f.x,
        y: prop === 'y' ? v : f.y,
        width: prop === 'width' ? v : f.width,
        height: prop === 'height' ? v : f.height,
      }, pageW, pageH)
      // Typing a narrower width must refit a character grid too, exactly as
      // dragging the resize handle does -- otherwise the panel becomes the
      // remaining route to an impossible cell_width.
      if (f.field_type === 'char_cells') {
        const nextMeta = refitCellMeta(f.meta, next.width)
        if (nextMeta !== f.meta) {
          onPatch({ ...next, meta: nextMeta } as Partial<DesignerField>)
          return
        }
      }
      onPatch(next)
      return
    }
    onPatch({ [prop]: clampProp(prop, v, pageW, pageH) } as Partial<DesignerField>)
  }

  const changeType = (t: string) => {
    onPatch({ field_type: t, meta: { ...(f.meta || {}), ...defaultMetaFor(t) } })
  }

  const copyBadge = () => {
    if (!f.field_key) return
    navigator.clipboard?.writeText(token(f.field_key)).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1000)
    })
  }

  return (
    <div className="p-3 space-y-3 text-xs">
      {/* Identity */}
      <div>
        <label className="block text-[10px] font-bold text-slate-500 uppercase tracking-wide mb-1">Label</label>
        <input value={f.label || ''} onChange={(e) => onPatch({ label: e.target.value })} type="text" className={inputCls} />
      </div>
      <div>
        <label className="block text-[10px] font-bold text-slate-500 uppercase tracking-wide mb-1 flex justify-between items-center">
          <span>Field Key</span>
          <span className="text-[9px] font-normal text-slate-400 font-sans">bind to candidate payload</span>
        </label>
        <input
          value={f.field_key || ''}
          onChange={(e) => onPatch({ field_key: e.target.value })}
          type="text"
          placeholder="e.g. full_name"
          className={`${inputCls} font-mono text-[11px] mb-1.5`}
        />
        <select
          value={suggestions.some((s) => s.key === f.field_key) ? f.field_key : ''}
          onChange={(e) => { if (e.target.value) onPatch({ field_key: e.target.value }) }}
          className="w-full text-[11px] text-slate-600 bg-slate-50 border border-slate-200 hover:border-slate-300 rounded px-2 py-1 outline-none font-mono focus:ring-1 focus:ring-blue-200 mb-2"
        >
          <option value="">-- Quick Choose Data Tag --</option>
          {suggestions.map((s) => (
            <option key={s.key + s.label} value={s.key}>{token(s.key)} - {s.label}</option>
          ))}
        </select>
        {f.field_key && (
          <div className="mt-1 flex items-center justify-between text-[10px] text-slate-500 bg-slate-50 border border-slate-100 rounded-md p-1.5">
            <span className="font-medium">Merge tags:</span>
            <code
              onClick={copyBadge}
              title="Click to copy syntax"
              className={`${copied ? 'bg-emerald-50 text-emerald-600 border-emerald-100' : 'bg-blue-50 text-blue-600 border-blue-100 hover:bg-blue-100'} font-bold px-2 py-0.5 rounded cursor-pointer border transition select-all font-mono text-[10px]`}
            >{copied ? 'Copied!' : token(f.field_key)}</code>
          </div>
        )}
      </div>
      <div>
        <label className="block text-[10px] font-bold text-slate-500 uppercase tracking-wide mb-1">Type</label>
        <select value={f.field_type} onChange={(e) => changeType(e.target.value)} className={inputCls}>
          {FIELD_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
        </select>
      </div>
      {f.field_type === 'static' && (
        <div>
          <label className="block text-[10px] font-bold text-slate-500 uppercase tracking-wide mb-1">Static value</label>
          <textarea value={f.static_value || ''} onChange={(e) => onPatch({ static_value: e.target.value })} rows={2} className={inputCls} />
        </div>
      )}

      {/* Geometry */}
      <div className="pt-2 border-t border-slate-100">
        <div className="text-[10px] font-bold text-slate-500 uppercase tracking-wide mb-1.5">Position &amp; size (PDF points)</div>
        <div className="grid grid-cols-2 gap-2">
          <label><L>X</L><NumInput step={0.5} value={round1(f.x)} onCommit={commitNum('x')} /></label>
          <label><L>Y <span className="text-[9px] text-slate-400">(bottom)</span></L><NumInput step={0.5} value={round1(f.y)} onCommit={commitNum('y')} /></label>
          <label><L>Width</L><NumInput value={round1(f.width)} onCommit={commitNum('width')} /></label>
          <label><L>Height</L><NumInput value={round1(f.height)} onCommit={commitNum('height')} /></label>
        </div>
      </div>

      {/* Typography */}
      <div className="pt-2 border-t border-slate-100">
        <div className="text-[10px] font-bold text-slate-500 uppercase tracking-wide mb-1.5">Typography</div>
        <div className="mb-2">
          <label>
            <L>Font Family</L>
            <select
              value={String(meta.font_name || '')}
              onChange={(e) => onPatchMeta('font_name', e.target.value)}
              className={`${inputCls} pr-6 text-ellipsis`}
              style={{ fontFamily: cssFontStack(meta.font_name), textOverflow: 'ellipsis' }}
              title={String(meta.font_name || 'Default (Auto-select)')}
            >
              {FONT_OPTIONS.map((o) => (
                <option key={o.label} value={o.value} style={{ fontFamily: o.css }}>{o.label}</option>
              ))}
            </select>
          </label>
        </div>
        <div className="grid grid-cols-2 gap-2 mb-2">
          <label><L>Font size (pt)</L><NumInput step={0.5} value={f.font_size ?? 11} onCommit={commitNum('font_size')} /></label>
          <label><L>Color</L>
            <input type="color" value={f.color || '#000000'} onChange={(e) => onPatch({ color: e.target.value })} className={`${inputCls} h-7 p-0.5`} />
          </label>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <L>Align</L>
            <div className="inline-flex w-full rounded-md border border-slate-300 overflow-hidden" role="group" aria-label="Text alignment">
              {(['left', 'center', 'right', 'justify'] as const).map((a) => (
                <button
                  key={a}
                  type="button"
                  title={`Align ${a}`}
                  onClick={() => onPatch({ align: a })}
                  className={`flex-1 px-0 py-1 text-[11px] transition ${(f.align || 'left') === a ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 hover:bg-slate-50'}`}
                >
                  <i className={`fa-solid fa-align-${a}`} />
                </button>
              ))}
            </div>
          </div>
          <label><L>Page</L><NumInput value={f.page || 1} onCommit={commitNum('page')} /></label>
        </div>
        <div className="mt-2">
          <L>Style</L>
          <div className="inline-flex rounded-md border border-slate-300 overflow-hidden" role="group" aria-label="Font style">
            <button
              type="button"
              title="Bold"
              onClick={() => onPatch({ font_bold: !f.font_bold })}
              className={`w-9 py-1 text-[12px] font-bold transition ${f.font_bold ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 hover:bg-slate-50'}`}
            >B</button>
            <button
              type="button"
              title="Italic"
              onClick={() => onPatch({ font_italic: !f.font_italic })}
              className={`w-9 py-1 text-[12px] italic border-l border-slate-300 transition ${f.font_italic ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 hover:bg-slate-50'}`}
            >I</button>
            <button
              type="button"
              title="Underline"
              onClick={() => onPatchMeta('font_underline', !meta.font_underline)}
              className={`w-9 py-1 text-[12px] underline border-l border-slate-300 transition ${meta.font_underline ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 hover:bg-slate-50'}`}
            >U</button>
            <button
              type="button"
              title="Strikethrough"
              onClick={() => onPatchMeta('font_strikethrough', !meta.font_strikethrough)}
              className={`w-9 py-1 text-[12px] line-through border-l border-slate-300 transition ${meta.font_strikethrough ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 hover:bg-slate-50'}`}
            >S</button>
          </div>
        </div>
      </div>

      {/* Extended typography */}
      <div className="pt-2 border-t border-slate-100">
        <div className="text-[10px] font-bold text-slate-500 uppercase tracking-wide mb-1.5">Layout</div>
        <div className="grid grid-cols-2 gap-2">
          <label><L>Multiline</L>
            <select value={meta.multiline ? 'yes' : 'no'} onChange={(e) => onPatchMeta('multiline', e.target.value === 'yes')} className={inputCls}>
              <option value="no">No</option>
              <option value="yes">Yes</option>
            </select>
          </label>
          <label><L>Padding (pt)</L><NumInput step={0.5} value={Number(meta.padding ?? 0)} onCommit={(v) => onPatchMeta('padding', v)} /></label>
          <label><L>Letter spacing (pt)</L><NumInput step={0.1} value={Number(meta.letter_spacing ?? 0)} onCommit={(v) => onPatchMeta('letter_spacing', v)} /></label>
          <label><L>Line H (multiplier)</L><NumInput step={0.05} value={Number(meta.line_height ?? 0)} onCommit={(v) => onPatchMeta('line_height', v)} /></label>
          <label><L>Text Transform</L>
            <select value={String((meta.text_transform as TextTransform) || 'none')} onChange={(e) => onPatchMeta('text_transform', e.target.value)} className={inputCls}>
              <option value="none">None</option>
              <option value="uppercase">Uppercase</option>
              <option value="lowercase">Lowercase</option>
              <option value="capitalize">Capitalize</option>
            </select>
          </label>
          <label><L>Opacity (0-100)</L><NumInput step={1} value={Number(meta.opacity ?? 100)} onCommit={(v) => onPatchMeta('opacity', Math.min(100, Math.max(0, v)))} /></label>
        </div>
        <div className="mt-2">
          <L>Background</L>
          <div className="flex items-center gap-2 mt-0.5">
            <label className="inline-flex items-center gap-1 text-[11px] text-slate-700 cursor-pointer">
              <input type="checkbox" checked={!meta.bg_enabled} onChange={(e) => onPatchMeta('bg_enabled', !e.target.checked)} /> None
            </label>
            {!!meta.bg_enabled && (
              <input type="color" value={String(meta.bg_color || '#ffffff')} onChange={(e) => onPatchMeta('bg_color', e.target.value)} className={`${inputCls} w-16 h-7 p-0.5`} />
            )}
          </div>
        </div>
      </div>

      {/* Type-specific meta */}
      {['char_cells', 'photo', 'barcode', 'trade_table'].includes(f.field_type) && (
        <div className="pt-2 border-t border-slate-100">
          <div className="text-[10px] font-bold text-slate-500 uppercase tracking-wide mb-1.5">Type-specific</div>

          {f.field_type === 'char_cells' && (
            <div className="grid grid-cols-2 gap-2">
              <label><L>Cell count</L><NumInput value={Number(meta.cell_count ?? 13)} onCommit={(v) => onPatchMeta('cell_count', v)} /></label>
              <label><L>Cell width</L><NumInput step={0.5} value={Number(meta.cell_width ?? 18)} onCommit={(v) => onPatchMeta('cell_width', v)} /></label>
              <label><L>Cell gap</L><NumInput step={0.5} value={Number(meta.cell_gap ?? 0)} onCommit={(v) => onPatchMeta('cell_gap', v)} /></label>
              <label><L>Format</L>
                <select value={String(meta.format ?? '')} onChange={(e) => onPatchMeta('format', e.target.value)} className={inputCls}>
                  <option value="">— none —</option>
                  <option value="digits">digits only</option>
                  <option value="alnum">alphanumeric</option>
                  <option value="ddmmyyyy">date → DDMMYYYY</option>
                  {/* "text" keeps SPACES so a name/address comb leaves a blank
                      box between words. Every other mode strips them, which is
                      correct for ID grids but ran words together on names. */}
                  <option value="text">text (keep spaces)</option>
                </select>
              </label>
              {/* Grouped grids (audit 2026-08-11).
                  Pre-printed government forms rarely use one even run of
                  boxes: a Pakistani CNIC is printed 5-7-1 with a wider gap
                  where each dash goes. Both render engines have supported
                  `group_sizes` for this, but there was no way to SET it --
                  so OEP Form "cnic" had no groups and 8 of its 13 digits
                  printed into the wrong pre-printed box, with no control an
                  operator could reach to fix it. */}
              <label className="col-span-2"><L>Groups (e.g. 5-7-1 for CNIC)</L>
                <input
                  type="text"
                  className={inputCls}
                  placeholder="leave empty for one even run"
                  defaultValue={Array.isArray(meta.group_sizes) ? (meta.group_sizes as number[]).join('-') : ''}
                  key={`gs-${f._uid}`}
                  onBlur={(e) => {
                    const raw = e.target.value.trim()
                    if (!raw) { onPatchMeta('group_sizes', undefined); return }
                    const parts = raw.split(/[-,\s]+/).filter(Boolean).map(Number)
                    if (parts.length && parts.every((n) => Number.isFinite(n) && n > 0)) {
                      onPatchMeta('group_sizes', parts)
                    }
                  }}
                />
              </label>
              {(() => {
                // Show whether the groups actually describe this many
                // characters. A mismatched list is silently ignored by both
                // engines (it falls back to an even run), which looks exactly
                // like "the setting did nothing" -- so say so here.
                const gs = Array.isArray(meta.group_sizes) ? (meta.group_sizes as number[]) : null
                if (!gs || !gs.length) return null
                const sum = gs.reduce((a, b) => a + Number(b || 0), 0)
                const count = Number(meta.cell_count ?? 13)
                const slots = sum === count ? sum + gs.length - 1 : count
                return (
                  <div className={`col-span-2 text-[10px] leading-tight ${sum === count ? 'text-slate-500' : 'text-amber-700 font-semibold'}`}>
                    {sum === count
                      ? `${count} characters across ${slots} printed boxes (${gs.length - 1} separator gap${gs.length === 2 ? '' : 's'}).`
                      : `Groups add up to ${sum} but cell count is ${count} — this will be IGNORED. Make them match.`}
                  </div>
                )
              })()}
              {/* Multi-row combs (audit 2026-08-12).
                  OEP form OEPE-RF-02 prints BOTH field 6 (Name) and field 12
                  (Address) as two stacked rows of boxes: row 2 continues the
                  same value. Every char_cells row used to start at character
                  0, so a second row could only repeat the beginning -- which
                  is why only row 1 was ever placed and a 38-character address
                  printed just its first 13 characters. Set this to row 1's
                  cell count on the second row. */}
              <label className="col-span-2"><L>Start at character (multi-row combs)</L>
                <NumInput value={Number(meta.char_offset ?? 0)} onCommit={(v) => onPatchMeta('char_offset', Math.max(0, Math.round(v)) || undefined)} />
              </label>
              {Number(meta.char_offset ?? 0) > 0 && (
                <div className="col-span-2 text-[10px] leading-tight text-slate-500">
                  Continuation row: skips the first {Number(meta.char_offset)} characters
                  (place this under a row of {Number(meta.char_offset)} cells).
                </div>
              )}
              <label className="col-span-2 inline-flex items-center gap-1 text-[11px] text-slate-700">
                <input type="checkbox" checked={!!meta.draw_boxes} onChange={(e) => onPatchMeta('draw_boxes', e.target.checked)} /> Draw cell boxes (debug)
              </label>
            </div>
          )}

          {f.field_type === 'photo' && (
            <div className="grid grid-cols-2 gap-2">
              <label><L>Fit</L>
                <select value={String(meta.fit ?? 'cover')} onChange={(e) => onPatchMeta('fit', e.target.value)} className={inputCls}>
                  <option value="cover">Cover</option>
                  <option value="contain">Contain</option>
                  <option value="fill">Fill</option>
                </select>
              </label>
              <label className="inline-flex items-center gap-1 text-[11px] text-slate-700 mt-4">
                <input type="checkbox" checked={meta.border !== false} onChange={(e) => onPatchMeta('border', e.target.checked)} /> Border
              </label>
            </div>
          )}

          {f.field_type === 'barcode' && (
            <div className="grid grid-cols-2 gap-2">
              <label className="col-span-2">
                <L>Barcode content</L>
                <textarea
                  value={String(meta.barcode_content ?? meta.content ?? '')}
                  onChange={(e) => onPatchMeta('barcode_content', e.target.value)}
                  rows={2}
                  placeholder="{{visa_number}}"
                  className={`${inputCls} font-mono text-[11px]`}
                />
                <span className="block text-[9px] text-slate-400 mt-0.5 leading-snug">
                  Combine merge fields and text, e.g. <code>{'{{passport_no}}-{{cnic_no}}'}</code>,{' '}
                  <code>{'E{{e_number}}'}</code> or <code>{'FILE-{{file_number}}'}</code>.
                  Each <code>{'{{key}}'}</code> is replaced at print time.
                </span>
              </label>
              <label><L>Bar height (pt)</L><NumInput value={Number(meta.bar_height ?? 26)} onCommit={(v) => onPatchMeta('bar_height', v)} /></label>
              <label><L>Bar width (pt)</L><NumInput step={0.1} value={Number(meta.bar_width ?? 0) || 0} onCommit={(v) => onPatchMeta('bar_width', v || '')} /></label>
              <label><L>Caption font</L><NumInput value={Number(meta.caption_font_size ?? meta.caption_font ?? 8)} onCommit={(v) => onPatchMeta('caption_font_size', v)} /></label>
              <label><L>Symbology</L>
                <select value={String(meta.symbology ?? meta.format ?? 'code128')} onChange={(e) => onPatchMeta('symbology', e.target.value)} className={inputCls}>
                  <option value="code128">Code 128</option>
                  <option value="code39">Code 39</option>
                  <option value="ean13">EAN-13</option>
                  <option value="qr">QR Code</option>
                </select>
              </label>
              <label className="col-span-2 inline-flex items-center gap-1 text-[11px] text-slate-700">
                <input type="checkbox" checked={meta.show_text !== false} onChange={(e) => onPatchMeta('show_text', e.target.checked)} /> Show human-readable text
              </label>
            </div>
          )}

          {f.field_type === 'trade_table' && (
            <div className="grid grid-cols-2 gap-2">
              <label className="col-span-2">
                <L>Columns (comma-separated keys)</L>
                <input
                  type="text"
                  value={Array.isArray(meta.columns)
                    ? (meta.columns as unknown[]).map((c) => (typeof c === 'string' ? c : (c as { key?: string })?.key || '')).filter(Boolean).join(',')
                    : String(meta.columns ?? '')}
                  onChange={(e) => onPatchMeta('columns', e.target.value)}
                  placeholder="_sr,trade,quantity,_assigned,_available,salary,contract_period"
                  className={`${inputCls} font-mono text-[11px]`}
                />
                <span className="block text-[9px] text-slate-400 mt-0.5 leading-snug">
                  Built-in keys: <code>_sr</code>, <code>_assigned</code>, <code>_available</code> +
                  JobCategory fields (<code>trade</code>, <code>quantity</code>, <code>salary</code>,{' '}
                  <code>contract_period</code>). Leave blank to use the default OEP set.
                </span>
              </label>
              <label><L>Row height (pt)</L><NumInput step={0.5} value={Number(meta.row_height ?? 18)} onCommit={(v) => onPatchMeta('row_height', v)} /></label>
              <label><L>Padding-X (pt)</L><NumInput step={0.5} value={Number(meta.padding_x ?? 3)} onCommit={(v) => onPatchMeta('padding_x', v)} /></label>
              <label><L>Max rows (0 = all)</L><NumInput value={Number(meta.max_rows ?? 0)} onCommit={(v) => onPatchMeta('max_rows', v)} /></label>
              <label><L>Header fill</L>
                <input type="color" value={String(meta.header_fill ?? '#e5e7eb')} onChange={(e) => onPatchMeta('header_fill', e.target.value)} className={`${inputCls} h-7 p-0`} />
              </label>
              <label className="inline-flex items-center gap-1 text-[11px] text-slate-700 mt-2">
                <input type="checkbox" checked={meta.show_header !== false} onChange={(e) => onPatchMeta('show_header', e.target.checked)} /> Show header
              </label>
              <label className="inline-flex items-center gap-1 text-[11px] text-slate-700 mt-2">
                <input type="checkbox" checked={meta.border !== false} onChange={(e) => onPatchMeta('border', e.target.checked)} /> Cell borders
              </label>
              <label className="inline-flex items-center gap-1 text-[11px] text-slate-700 mt-1 col-span-2">
                <input type="checkbox" checked={!!meta.zebra} onChange={(e) => onPatchMeta('zebra', e.target.checked)} /> Zebra-stripe alternate rows
              </label>
            </div>
          )}
        </div>
      )}

      {/* Footer actions */}
      <div className="pt-3 border-t border-slate-100 flex items-center justify-between">
        <button type="button" onClick={onDuplicate} className="text-[11px] text-slate-600 hover:text-blue-600">
          <i className="fa-regular fa-clone mr-1" /> Duplicate
        </button>
        <button type="button" onClick={onDelete} className="text-[11px] text-rose-600 hover:bg-rose-50 px-2 py-1 rounded">
          <i className="fa-regular fa-trash-can mr-1" /> Remove
        </button>
      </div>
    </div>
  )
}
