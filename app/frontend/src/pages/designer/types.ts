/**
 * Document Designer — shared types + per-type defaults.
 * Mirrors the DocumentField / DocumentTemplate schemas server-side and the
 * defaults used by the reference designer, so both implementations
 * produce identical persisted rows.
 */

export type FieldMeta = Record<string, unknown>

export type FieldAlign = 'left' | 'center' | 'right' | 'justify'
export type TextTransform = 'none' | 'uppercase' | 'lowercase' | 'capitalize'

export type FieldType =
  | 'text' | 'static' | 'date' | 'checkbox' | 'char_cells'
  | 'photo' | 'barcode' | 'arabic' | 'trade_table'

export const FIELD_TYPES: { value: FieldType; label: string }[] = [
  { value: 'text',        label: 'Text' },
  { value: 'static',      label: 'Static text' },
  { value: 'date',        label: 'Date' },
  { value: 'checkbox',    label: 'Checkbox' },
  { value: 'char_cells',  label: 'Character cells (CNIC / passport)' },
  { value: 'photo',       label: 'Photo' },
  { value: 'barcode',     label: 'Barcode' },
  { value: 'arabic',      label: 'Arabic (RTL)' },
  { value: 'trade_table', label: 'Trade table' },
]

/** Text-like types rendered with the baseline-locked body span. */
export const TEXT_LIKE: FieldType[] = ['text', 'date', 'static', 'char_cells', 'arabic']

export interface DesignerField {
  _uid: string
  id: number | null
  label: string
  field_key: string
  field_type: FieldType | string
  static_value: string
  x: number
  y: number
  width: number
  height: number
  font_size: number
  font_bold: boolean
  font_italic: boolean
  color: string
  align: FieldAlign
  page: number
  meta: FieldMeta
}

export interface Template {
  id: number
  name: string
  description: string
  category: string
  data_source: string
  is_active: boolean
  background_image: string
  page_width: number
  page_height: number
  page_size: string
  orientation: 'portrait' | 'landscape'
  fields?: Partial<DesignerField>[]
}

export interface PaletteField {
  key: string
  label: string
  field_type: string
  arabic?: boolean
  default_static?: string
  meta?: FieldMeta
}
export interface PaletteGroup { group: string; fields: PaletteField[] }
export interface Palette { groups: PaletteGroup[]; flat?: PaletteField[] }

export interface BackgroundOpt {
  filename: string
  url?: string
  size?: number
  width?: number | null
  height?: number | null
}

/** Default marker sizes per type (pt). */
export function defaultWidthFor(t: string): number {
  return ({ photo: 120, checkbox: 14, barcode: 160, trade_table: 300, char_cells: 260 } as Record<string, number>)[t] || 180
}
export function defaultHeightFor(t: string): number {
  return ({ photo: 140, checkbox: 14, barcode: 32, trade_table: 80, char_cells: 22 } as Record<string, number>)[t] || 18
}

/**
 * Extended meta keys (stored in field.meta JSON — no DB migration needed):
 *   font_underline: boolean     — underline text
 *   font_strikethrough: boolean — strikethrough text
 *   multiline: boolean          — allow wrapping (default false)
 *   padding: number             — inner padding (pt)
 *   letter_spacing: number      — letter-spacing (pt)
 *   line_height: number         — line-height multiplier (e.g. 1.2)
 *   text_transform: TextTransform — none/uppercase/lowercase/capitalize
 *   opacity: number             — 0-100 (100 = fully opaque)
 *   bg_enabled: boolean         — field has a background colour
 *   bg_color: string            — hex colour when bg_enabled
 */

/** Default type-specific meta. */
export function defaultMetaFor(t: string): FieldMeta {
  if (t === 'char_cells') return { cell_count: 13, cell_width: 18, cell_gap: 0, format: '', draw_boxes: false }
  if (t === 'photo')      return { fit: 'cover', border: true }
  if (t === 'barcode')    return { symbology: 'code128', barcode_content: '{{visa_number}}', bar_height: 26, caption_font_size: 8, show_text: true }
  if (t === 'trade_table') return { cols: ['category', 'qty', 'assigned', 'available'], row_height: 18 }
  return {}
}

/** Font-awesome icon class per palette field type. */
export function iconForType(t: string): string {
  return ({
    text:        'fa-regular fa-keyboard',
    static:      'fa-regular fa-comment-dots',
    date:        'fa-regular fa-calendar',
    checkbox:    'fa-regular fa-square-check',
    char_cells:  'fa-solid fa-grip-lines-vertical',
    photo:       'fa-regular fa-image',
    barcode:     'fa-solid fa-barcode',
    arabic:      'fa-solid fa-language',
    trade_table: 'fa-solid fa-table',
  } as Record<string, string>)[t] || 'fa-regular fa-circle'
}

/**
 * Font families offered in the Typography panel.
 *
 * `value` is the stored / PDF-engine (ReportLab) font identifier — the single
 * source of truth persisted on the field. `css` is the *browser* font stack the
 * canvas must use to render the SAME face faithfully, because CSS does NOT
 * understand ReportLab names like "Times-Roman" or "DejaVuSans". Previously the
 * raw ReportLab value was pushed straight into CSS `font-family`, so the browser
 * silently fell back to the default face and the chosen font never appeared on
 * the canvas (only in the exported PDF). Mapping value → css fixes that so the
 * designer preview matches the print/PDF output.
 */
export interface FontOption { value: string; label: string; css: string }
export const FONT_OPTIONS: FontOption[] = [
  { value: '',                label: 'Default (Auto-select)', css: "'Inter', 'Segoe UI', Arial, Helvetica, sans-serif" },
  { value: 'Arial',           label: 'Arial',                 css: "Arial, Helvetica, 'Liberation Sans', sans-serif" },
  { value: 'Helvetica',       label: 'Helvetica',             css: "Helvetica, 'Helvetica Neue', Arial, sans-serif" },
  { value: 'Times New Roman', label: 'Times New Roman',       css: "'Times New Roman', Times, 'Liberation Serif', serif" },
  { value: 'Georgia',         label: 'Georgia',               css: "Georgia, 'Times New Roman', serif" },
  { value: 'Verdana',         label: 'Verdana',               css: "Verdana, Geneva, 'DejaVu Sans', sans-serif" },
  { value: 'Tahoma',          label: 'Tahoma',                css: "Tahoma, Verdana, Geneva, sans-serif" },
  { value: 'Courier New',     label: 'Courier New',           css: "'Courier New', Courier, 'Liberation Mono', monospace" },
  { value: 'Noto Arabic',     label: 'Noto Arabic',           css: "'Noto Sans Arabic', 'Noto Naskh Arabic', Tahoma, sans-serif" },
  { value: 'DejaVu Sans',     label: 'DejaVu Sans (Unicode)', css: "'DejaVu Sans', Verdana, sans-serif" },
]

/**
 * Resolve a stored font name to a browser font stack for the canvas. CSS does
 * not understand ReportLab identifiers, and older rows may already store those
 * (e.g. "Times-Roman", "DejaVuSans"), so we accept both the new human names and
 * the legacy PDF names. Falls back to the default UI stack when unknown/empty.
 */
const _LEGACY_CSS: Record<string, string> = {
  'Times-Roman': "'Times New Roman', Times, serif",
  'Times-Bold': "'Times New Roman', Times, serif",
  'Helvetica-Bold': "Helvetica, Arial, sans-serif",
  'Courier': "'Courier New', Courier, monospace",
  'Courier-Bold': "'Courier New', Courier, monospace",
  'DejaVuSans': "'DejaVu Sans', Verdana, sans-serif",
  'DejaVuSans-Bold': "'DejaVu Sans', Verdana, sans-serif",
  'NotoSansArabic': "'Noto Sans Arabic', 'Noto Naskh Arabic', Tahoma, sans-serif",
}
export function cssFontStack(fontName: unknown): string {
  const v = String(fontName || '')
  if (!v) return "'Inter', 'Segoe UI', Arial, Helvetica, sans-serif"
  const hit = FONT_OPTIONS.find((o) => o.value === v && v !== '')
  if (hit) return hit.css
  if (_LEGACY_CSS[v]) return _LEGACY_CSS[v]
  return `'${v}', 'Inter', Arial, Helvetica, sans-serif`
}

/** Description "meta fence" — letterhead / depositor / notes / bg_fit are
 *  persisted inside an HTML-comment appended to the description string. */
export const META_FENCE_RE = /<!--designer:(.+?)-->/

export type BgFit = 'fill' | 'contain' | 'actual'
export const BG_FIT_VALUES: BgFit[] = ['fill', 'contain', 'actual']

export interface DescMeta { letterhead?: boolean; depositor?: boolean; notes?: string; bg_fit?: BgFit }

export function parseMetaFromDescription(desc: string): DescMeta {
  const m = (desc || '').match(META_FENCE_RE)
  if (!m) return {}
  try { return JSON.parse(m[1]) } catch { return {} }
}

export function serializeDescription(visible: string, meta: Required<DescMeta>): string {
  const clean = (visible || '').replace(META_FENCE_RE, '').trim()
  const payload = { letterhead: meta.letterhead, depositor: meta.depositor, notes: meta.notes || '', bg_fit: meta.bg_fit || 'fill' }
  const fence = `<!--designer:${JSON.stringify(payload)}-->`
  return clean ? `${clean}\n${fence}` : fence
}

let __uid = 1
export const newUid = () => `f${__uid++}`

/** Reduce any stored background reference to a bare filename. */
export function bgFilename(v: string): string {
  if (!v) return ''
  let s = String(v).trim()
  if (/^https?:\/\//i.test(s)) { try { s = new URL(s).pathname } catch { /* keep */ } }
  return s.split('/').pop() || ''
}

/** Resolve a background reference to an absolute URL for <img src>. */
export function bgSrc(v: string): string {
  const s = (v || '').trim()
  if (!s) return ''
  if (/^https?:\/\//i.test(s)) return s
  if (s.startsWith('/static/')) return s
  if (s.startsWith('static/')) return '/' + s
  return `/static/pdf_backgrounds/${s}`
}

/** Double-brace merge token for a field key (e.g. "{{full_name}}"). */
export const token = (key: string) => `{{${key || ''}}}`
