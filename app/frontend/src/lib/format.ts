// ---------------------------------------------------------------------------
// Human-friendly formatting helpers.
//
// The backend stores many values as machine slugs / enums (e.g. "docs_pending",
// "visa_process", "protector_documents") and raw uploaded file names
// (e.g. "allied_bank_form.jpg"). Those must NEVER reach the end user as-is.
// These helpers turn any such raw value into a clean, title-cased label.
// ---------------------------------------------------------------------------

/** Known acronyms / special-cased words that should keep specific casing. */
const SPECIAL: Record<string, string> = {
  cnic: 'CNIC',
  oep: 'OEP',
  nbp: 'NBP',
   ksa: 'KSA',
  uae: 'UAE',
  id: 'ID',
  no: 'No.',
  pk: 'PK',
  ai: 'AI',
  hr: 'HR',
  cv: 'CV',
}

/** Canonical, hand-written labels for well-known status / category slugs. */
const KNOWN_LABELS: Record<string, string> = {
  // Candidate workflow stages
  new: 'Received',
  docs_pending: 'Documents Pending',
  'documents-pending': 'Documents Pending',
  medical: 'Medical',
  protector: 'Protector',
  e_number: 'E-Number',
  'f-number': 'File Number Issued',
  f_number: 'File Number Issued',
  visa_stamping: 'Visa Stamping',
  travel_ready: 'Travel Ready',
  deployed: 'Deployed',
  active: 'Active',
  inactive: 'Inactive',
  pending: 'Pending',
  completed: 'Completed',
  cancelled: 'Cancelled',
  closed: 'Closed',
  // Document data sources
  candidate: 'Candidate',
  demand: 'Demand File',
  visa_process: 'Visa Process',
  // Document categories
  protector_documents: 'Protector Documents',
  permission_documents: 'Permission Documents',
  custom: 'Custom',
}

/**
 * Convert a raw slug / enum / snake- or kebab-case string into a clean,
 * human-friendly Title Case label.
 *
 *   humanize('docs_pending')        -> 'Documents Pending'
 *   humanize('visa_process')        -> 'Visa Process'
 *   humanize('protector_documents') -> 'Protector Documents'
 *   humanize('cnic_front')          -> 'CNIC Front'
 */
export function humanize(value?: string | null): string {
  if (value === undefined || value === null) return ''
  const raw = String(value).trim()
  if (!raw) return ''

  const key = raw.toLowerCase()
  if (KNOWN_LABELS[key]) return KNOWN_LABELS[key]

  // If it already looks like a proper human label (has spaces & a capital,
  // and no underscores/hyphens), leave it untouched.
  if (/[A-Z]/.test(raw) && /\s/.test(raw) && !/[_-]/.test(raw)) return raw

  return raw
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .split(' ')
    .map((w) => {
      const lw = w.toLowerCase()
      if (SPECIAL[lw]) return SPECIAL[lw]
      return w.charAt(0).toUpperCase() + w.slice(1).toLowerCase()
    })
    .join(' ')
}

/**
 * Detect template/placeholder strings that were never filled in
 * (e.g. "INSERT SPONSOR PHONE NUMBER", "TBD", "N/A", "xxxx") so they can be
 * suppressed instead of shown to the user as if they were real data.
 */
export function isPlaceholderValue(value?: unknown): boolean {
  if (typeof value !== 'string') return false
  const v = value.trim()
  if (!v) return true
  const lower = v.toLowerCase()
  if (/^(n\/?a|na|tbd|tba|none|null|undefined|pending|xxx+|---+|\.\.\.+)$/i.test(lower)) return true
  // Bracketed or underscore template tokens: [field], {field}, <field>, ___
  if (/^[[{<].*[\]}>]$/.test(v) || /^_+$/.test(v)) return true
  // ALL-CAPS instructional placeholders that start with a directive verb.
  if (/^[A-Z][A-Z\s./-]{6,}$/.test(v) && /^(INSERT|ENTER|TYPE|ADD|FILL|PUT|PROVIDE|WRITE)\b/.test(v)) return true
  return false
}

/** Return the value if it is real data, otherwise an empty string. */
export function cleanValue(value?: unknown): string {
  if (value === undefined || value === null) return ''
  if (typeof value !== 'string') return String(value)
  return isPlaceholderValue(value) ? '' : value
}

/**
 * Turn a raw uploaded file name / path into a readable document name.
 *
 *   prettifyFileName('uploads/allied_bank_form.jpg') -> 'Allied Bank Form'
 *   prettifyFileName('dogar_letterhead.png')          -> 'Dogar Letterhead'
 */
export function prettifyFileName(value?: string | null): string {
  if (!value) return ''
  const base = String(value).split('/').pop() || ''
  const noExt = base.replace(/\.[a-z0-9]+$/i, '')
  return humanize(noExt) || base
}

// ---------------------------------------------------------------------------
// FIX 9 — Time-of-day greeting used in the header ("Good afternoon, Demo!")
// and on the Dashboard welcome line ("Good morning, Administrator!").
//
// This function was previously duplicated verbatim in both Dashboard.tsx and
// Topbar.tsx. Two independent copies meant a change in one place (say, adding
// a "Good night" bucket after 21:00) would silently disagree with the other.
// Consolidated here as the single source of truth.
//
// Boundaries: < 12:00 morning, < 17:00 afternoon, otherwise evening — matches
// the reference enterprise portal's greeting logic that both call-sites were
// modelling. `now` is injectable to keep the function unit-testable without
// having to freeze system time.
// ---------------------------------------------------------------------------
export function greeting(now: Date = new Date()): string {
  const h = now.getHours()
  if (h < 12) return 'Good morning'
  if (h < 17) return 'Good afternoon'
  return 'Good evening'
}
