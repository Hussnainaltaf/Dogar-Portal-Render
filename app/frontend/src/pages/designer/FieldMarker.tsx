/**
 * A single absolutely-positioned field marker on the designer canvas.
 * Renders the field marker overlay on the document canvas:
 *   - box at the field's real geometry (hit-area == what the user manipulates)
 *   - baseline-locked body span for text-like fields (designer == print == PDF)
 *   - live JsBarcode / QR preview for barcode fields
 *   - photo / checkbox / trade_table visual treatments
 *   - selected outline + tag + resize handle
 *   - live-data preview override (green highlight + real value)
 */
import { memo } from 'react'
import type { DesignerField } from './types'
import { TEXT_LIKE, token, cssFontStack } from './types'
import { PT_TO_CSS, baselineBodyTop, cellSlotSpan, cellSlots, effectiveHeight, ptToCssLeft, ptToCssTop } from './engine'
import { BarcodePreview } from './barcode'

interface Props {
  field: DesignerField
  pageH: number
  selected: boolean
  previewValue: string | null   // non-null ⇒ live-data preview mode with a value
  /** ADDRESS CAPACITY POLICY (app/services/comb_capacity.py).
   *
   * Exactly the characters this comb ROW should print, decided by the server
   * — abbreviation, word-boundary truncation and multi-row capacity are all
   * resolved there. Deliberately NOT recomputed here: the ladder must live in
   * one place or the three renderers (PDF / HTML print / this preview) drift
   * apart, which is the documented history of this field type.
   *
   * null ⇒ not in preview mode, fall back to the design-time token display. */
  combRow?: string | null
  /** The whole-value verdict, so the marker can flag review-required. */
  combFit?: { status: string; review_required: boolean; reason: string; dropped: string } | null
  onPointerDown: (uid: string, e: React.PointerEvent) => void
  onResizeStart: (uid: string, e: React.PointerEvent) => void
  onDoubleClick: (uid: string) => void
}

function FieldMarkerImpl({ field: f, pageH, selected, previewValue, combRow = null, combFit = null, onPointerDown, onResizeStart, onDoubleClick }: Props) {
  const wPt = Number(f.width) || 100
  const fs = Number(f.font_size) || 11
  // COORDINATE PARITY: use the stored height VERBATIM (fallback only when
  // empty), exactly like the print view (documents.py) and pdf_engine.py.
  // The old Math.max(height, fs*1.25) inflation shifted the box top — and
  // therefore the baseline — whenever height < fs*1.25, causing a visible
  // designer↔print drift for large-font/small-box fields.
  const hPt = effectiveHeight(Number(f.height) || 0, fs)
  const isText = TEXT_LIKE.includes(f.field_type as (typeof TEXT_LIKE)[number])
  const ftype = f.field_type || 'text'
  const meta = f.meta || {}

  const hasPreview = previewValue !== null && String(previewValue).trim() !== ''

  // Extended meta properties
  const metaPadding = Number(meta.padding ?? 0)
  const metaLetterSpacing = Number(meta.letter_spacing ?? 0)
  const metaLineHeight = Number(meta.line_height ?? 0)
  const metaTextTransform = String(meta.text_transform || 'none') as React.CSSProperties['textTransform']
  const metaOpacity = Number(meta.opacity ?? 100)
  const metaBgEnabled = !!meta.bg_enabled
  const metaBgColor = String(meta.bg_color || '#ffffff')
  const metaUnderline = !!meta.font_underline
  const metaStrikethrough = !!meta.font_strikethrough
  const metaMultiline = !!meta.multiline

  // Build textDecoration
  const deco = [metaUnderline && 'underline', metaStrikethrough && 'line-through'].filter(Boolean).join(' ') || 'none'

  // Type-specific box styling for each field type.
  const boxStyle: React.CSSProperties = {
    position: 'absolute',
    boxSizing: 'border-box',
    left: ptToCssLeft(Number(f.x) || 0),
    top: ptToCssTop(Number(f.y) || 0, hPt, pageH),
    width: wPt * PT_TO_CSS,
    height: hPt * PT_TO_CSS,
    border: '1px dashed transparent',
    background: metaBgEnabled ? metaBgColor : 'transparent',
    color: f.color || '#0f172a',
    // Map the stored PDF/font identifier to a real CSS font stack so the
    // canvas renders the SAME face the PDF will use (fixes fonts never
    // appearing on-canvas because "Times-Roman" etc. isn't valid CSS).
    fontFamily: cssFontStack(meta.font_name),
    lineHeight: metaLineHeight > 0 ? metaLineHeight : 1.05,
    whiteSpace: metaMultiline ? 'pre-wrap' : 'nowrap',
    overflow: 'visible',
    cursor: 'move',
    userSelect: 'none',
    padding: metaPadding > 0 ? metaPadding * PT_TO_CSS : 0,
    fontSize: fs * PT_TO_CSS,
    textAlign: f.align || 'left',
    fontWeight: f.font_bold ? 700 : 400,
    fontStyle: f.font_italic ? 'italic' : 'normal',
    textDecoration: deco,
    letterSpacing: metaLetterSpacing > 0 ? `${metaLetterSpacing}px` : undefined,
    textTransform: metaTextTransform !== 'none' ? metaTextTransform : undefined,
    opacity: metaOpacity < 100 ? metaOpacity / 100 : undefined,
    touchAction: 'none',
  }
  if (ftype === 'arabic') {
    boxStyle.direction = 'rtl'
    boxStyle.textAlign = 'right'
    boxStyle.fontFamily = "'Noto Sans Arabic', 'Tahoma', sans-serif"
  } else if (ftype === 'checkbox') {
    Object.assign(boxStyle, { border: '1px solid #475569', background: '#fff', textAlign: 'center' as const, color: '#0f172a', fontWeight: 700 })
  } else if (ftype === 'photo') {
    Object.assign(boxStyle, {
      background: 'repeating-linear-gradient(45deg, #f1f5f9 0 6px, #e2e8f0 6px 12px)',
      border: '1px solid #94a3b8', textAlign: 'center' as const, color: '#64748b', fontSize: 10,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    })
  } else if (ftype === 'barcode') {
    Object.assign(boxStyle, {
      background: '#fff', border: '1px solid #475569', color: '#0f172a', fontSize: 8,
      fontFamily: "ui-monospace, 'Courier New', monospace", textAlign: 'center' as const, lineHeight: 1, overflow: 'hidden',
    })
  } else if (ftype === 'trade_table') {
    Object.assign(boxStyle, { background: 'rgba(14,165,233,0.06)', border: '1px solid #0ea5e9', color: '#0369a1', fontSize: 10 })
  }
  if (selected) {
    boxStyle.border = '1px dashed var(--brand, #0071e3)'
    boxStyle.borderColor = 'var(--brand, #0071e3)'
    boxStyle.background = 'rgba(37,99,235,0.08)'
    boxStyle.boxShadow = '0 0 0 1px var(--brand, #0071e3), 0 4px 12px rgba(37,99,235,0.18)'
    boxStyle.zIndex = 50
  }
  if (hasPreview && isText) {
    boxStyle.background = 'rgba(16,185,129,0.06)'
    boxStyle.borderColor = '#10b981'
    boxStyle.border = '1px dashed #10b981'
  }

  // Body content — token / static / preview value, exactly like buildMarker().
  const bcContent = String((meta.barcode_content as string) || (meta.content as string) || '')
  const tokenBody = f.field_key && !String(f.field_key).startsWith('__')
    ? token(f.field_key)
    : (f.static_value || f.label || '')
  const textBody = hasPreview ? String(previewValue) : (ftype === 'static' ? (f.static_value || f.label || '') : tokenBody)

  const bodyStyle: React.CSSProperties = {
    fontWeight: f.font_bold ? 700 : 400,
    fontStyle: f.font_italic ? 'italic' : 'normal',
    textAlign: f.align || 'left',
    textDecoration: deco,
    letterSpacing: metaLetterSpacing > 0 ? `${metaLetterSpacing}px` : undefined,
    textTransform: metaTextTransform !== 'none' ? metaTextTransform : undefined,
    color: '#000',
  }

  let body: React.ReactNode
  if (ftype === 'char_cells') {
    // DESIGNER BLINDNESS (audit 2026-08-12): char_cells is in TEXT_LIKE, so the
    // canvas used to draw the grid as ONE continuous string -- the operator saw
    // "3450303592411" flowing left-to-right at the field's font size and had no
    // way whatsoever to tell whether the digits would land in the pre-printed
    // boxes of the government form. Every geometry defect in this engine (the
    // missing 5-7-1 CNIC grouping, the wrong pitch inherited on resize, the
    // pt-emitted-as-px shrink) was therefore INVISIBLE on the screen whose
    // entire job is to show it.
    //
    // Now the marker positions one span per cell using the SAME slot maths as
    // pdf_engine._draw_char_cells and the HTML print path, honouring
    // group_sizes (blank slot per printed separator) and char_offset
    // (continuation row). What you see is what prints.
    const bodyTop = baselineBodyTop(Number(f.y) || 0, hPt, fs, pageH)
    const count = Math.max(0, Math.floor(Number(meta.cell_count) || 0))
    const gap = Number(meta.cell_gap) || 0
    const slots = cellSlots(count, meta.group_sizes)
    const span = cellSlotSpan(count, meta.group_sizes)
    // Fall back to the box-fitted pitch when meta.cell_width is unset, exactly
    // like the server: never invent a pitch the engines would not use.
    const stored = Number(meta.cell_width) || 0
    const pitch = stored > 0
      ? stored
      : (span > 0 ? (wPt - gap * Math.max(0, span - 1)) / span : 0)
    const offset = Math.max(0, Math.floor(Number(meta.char_offset) || 0))
    const raw = hasPreview ? String(previewValue) : String(tokenBody)
    // ADDRESS CAPACITY POLICY -- see app/services/comb_capacity.py.
    //
    // In preview mode the server has already decided this row's characters
    // (normalised, abbreviated where attested, cut at a word boundary if it
    // could not fit, and sliced to this row's char_offset using the REAL
    // multi-row capacity). Use that verbatim.
    //
    // The client used to do this itself: strip separators, then
    // `.slice(offset)` and let the render loop drop anything past `count`. That
    // silently truncated 83.4% of addresses mid-word, and -- worse -- it was a
    // SECOND implementation of the rule, so the preview could agree with itself
    // while disagreeing with the PDF. Recomputing here would reintroduce
    // exactly that class of bug.
    //
    // The `stripped` fallback below is only for design time (no record loaded),
    // where there is no value to apply a policy to -- just a {{token}} to show.
    const keepSpaces = String(meta.format || '').toLowerCase() === 'text'
    const stripped = keepSpaces
      ? raw.replace(/-/g, '').replace(/\s+/g, ' ').trim()
      : raw.replace(/[-\s]/g, '')
    const chars = combRow !== null
      ? combRow
      : (hasPreview ? stripped : raw).slice(offset)
    body = (
      <>
        {slots.slice(0, count).map((slot, i) => (
          <span
            key={i}
            style={{
              ...bodyStyle,
              position: 'absolute',
              left: slot * (pitch + gap) * PT_TO_CSS,
              width: pitch * PT_TO_CSS,
              top: bodyTop,
              height: fs * PT_TO_CSS,
              lineHeight: `${fs * PT_TO_CSS}px`,
              textAlign: 'center',
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              pointerEvents: 'none',
              display: 'block',
            }}
          >{i < chars.length ? chars[i] : ''}</span>
        ))}
        {/* REVIEW-REQUIRED BADGE.
            The operator must be able to SEE that this value did not fit before
            they print a government form. A log line does not reach them; the
            document looking complete is exactly the failure mode this policy
            exists to end. The reason (including the exact dropped text) is in
            the title so hovering explains it without cluttering the canvas. */}
        {combFit?.review_required && (
          <span
            data-comb-review={combFit.status}
            title={combFit.reason}
            style={{
              position: 'absolute', top: -7, right: -7,
              width: 14, height: 14, borderRadius: 7,
              background: combFit.status === 'capacity_exceeded' ? '#dc2626' : '#f59e0b',
              color: '#fff', fontSize: 10, lineHeight: '14px',
              textAlign: 'center', fontWeight: 700,
              pointerEvents: 'auto', cursor: 'help', zIndex: 5,
            }}
          >!</span>
        )}
      </>
    )
  } else if (isText) {
    const bodyTop = baselineBodyTop(Number(f.y) || 0, hPt, fs, pageH)
    body = (
      <span
        style={{
          ...bodyStyle,
          position: 'absolute', left: 0, right: 0,
          top: bodyTop,
          height: metaMultiline ? 'auto' : fs * PT_TO_CSS,
          lineHeight: metaLineHeight > 0 ? metaLineHeight : `${fs * PT_TO_CSS}px`,
          whiteSpace: metaMultiline ? 'pre-wrap' : 'nowrap',
          overflow: metaMultiline ? 'visible' : 'hidden',
          textOverflow: metaMultiline ? undefined : 'ellipsis',
          pointerEvents: 'none', display: 'block',
        }}
      >{textBody}</span>
    )
  } else if (ftype === 'barcode') {
    const bcValue = String(bcContent || f.static_value || f.field_key || '123456789')
    body = <BarcodePreview value={hasPreview ? String(previewValue) : bcValue} meta={meta} />
  } else if (ftype === 'photo') {
    body = <span style={{ pointerEvents: 'none' }}>📷 photo</span>
  } else if (ftype === 'checkbox') {
    body = <span style={{ ...bodyStyle, pointerEvents: 'none' }}>{f.static_value || '☐'}</span>
  } else if (ftype === 'trade_table') {
    body = <span style={{ ...bodyStyle, color: '#0369a1', pointerEvents: 'none' }}>▦ trade table</span>
  } else {
    body = <span style={{ ...bodyStyle, pointerEvents: 'none' }}>{textBody}</span>
  }

  return (
    <div
      data-role="field-marker"
      data-uid={f._uid}
      style={boxStyle}
      onPointerDown={(e) => onPointerDown(f._uid, e)}
      onDoubleClick={(e) => { e.stopPropagation(); onDoubleClick(f._uid) }}
    >
      {/* Tag (label chip) — visible only when selected. */}
      {selected && (
        <span style={{
          position: 'absolute', top: -16, left: -1, fontSize: 9,
          background: 'var(--brand, #0071e3)', color: '#fff', padding: '1px 5px',
          borderRadius: '3px 3px 0 0', fontFamily: 'ui-sans-serif, system-ui, sans-serif',
          fontWeight: 600, whiteSpace: 'nowrap', pointerEvents: 'none',
        }}>{f.label || f.field_key}</span>
      )}
      {body}
      {/* Resize handle — visible only when selected. */}
      {selected && (
        <span
          data-role="resize-handle"
          onPointerDown={(e) => onResizeStart(f._uid, e)}
          style={{
            position: 'absolute', right: -4, bottom: -4, width: 9, height: 9,
            background: 'var(--brand, #0071e3)', border: '1px solid white',
            borderRadius: 2, cursor: 'nwse-resize', touchAction: 'none',
          }}
        />
      )}
    </div>
  )
}

const FieldMarker = memo(FieldMarkerImpl)
export default FieldMarker
