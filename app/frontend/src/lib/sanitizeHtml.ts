/**
 * sanitizeHtml — dependency-free, allowlist-based HTML sanitizer for the
 * document designer's rich-text (contenteditable) editor.
 *
 * Why this exists (M-audit 2026-07-24):
 *   ContentTab.tsx round-trips WYSIWYG content through
 *   `element.innerHTML` — reading it back to persist, and writing stored
 *   values back in. Because that HTML is saved to the database and later
 *   rendered for *other* users (and merged into printed documents), an
 *   untrusted editor could store `<script>`, `<img onerror=…>`, or
 *   `javascript:` payloads → stored XSS. We now sanitize on both load and
 *   save so a payload can neither be persisted nor re-rendered.
 *
 * Implementation notes:
 *   - Parsing happens inside a detached <template>.content DocumentFragment.
 *     Unlike assigning to a live element's innerHTML, template parsing does
 *     NOT execute scripts and does NOT fire resource-load handlers
 *     (e.g. <img onerror>), so the parse step itself is safe.
 *   - We keep only an allowlist of formatting/structure tags and attributes
 *     that the editor toolbar can actually produce (bold, lists, alignment,
 *     tables, inline style for the column/trade-table helpers).
 *   - `style` is allowed but scrubbed of `expression(...)` and `url(...)`
 *     (the latter blocks `url(javascript:…)` and data-exfil via CSS).
 *   - `{{merge_field}}` tokens are plain text and pass through untouched.
 */

// Tags the editor toolbar / insert helpers legitimately produce.
const ALLOWED_TAGS = new Set([
  'P', 'BR', 'DIV', 'SPAN',
  'B', 'STRONG', 'I', 'EM', 'U', 'S', 'STRIKE', 'SUB', 'SUP',
  'H1', 'H2', 'H3', 'H4', 'H5', 'H6',
  'BLOCKQUOTE', 'PRE', 'CODE',
  'UL', 'OL', 'LI',
  'TABLE', 'THEAD', 'TBODY', 'TFOOT', 'TR', 'TD', 'TH', 'CAPTION', 'COLGROUP', 'COL',
  'FONT', 'HR',
])

// Attributes allowed on any surviving element.
const ALLOWED_ATTRS = new Set([
  'style', 'align', 'colspan', 'rowspan', 'width', 'height',
  'color', 'face', 'size', // legacy <font> attrs execCommand emits
  'start', 'type', // list attrs
])

function scrubStyle(value: string): string {
  // Strip anything that can execute or fetch: CSS expressions and url(...).
  return value
    .replace(/expression\s*\(/gi, '')
    .replace(/url\s*\([^)]*\)/gi, '')
    .replace(/[<>]/g, '')
    .trim()
}

function cleanElement(el: Element) {
  // Remove disallowed elements entirely (unwrap would risk keeping payload
  // attributes; for editor content, dropping the node is acceptable).
  if (!ALLOWED_TAGS.has(el.tagName)) {
    el.remove()
    return
  }

  // Copy attribute list first — we mutate while iterating.
  for (const attr of Array.from(el.attributes)) {
    const name = attr.name.toLowerCase()
    const val = attr.value

    // Drop every event handler (onclick, onerror, onload, …).
    if (name.startsWith('on')) {
      el.removeAttribute(attr.name)
      continue
    }
    if (!ALLOWED_ATTRS.has(name)) {
      el.removeAttribute(attr.name)
      continue
    }
    if (name === 'style') {
      const cleaned = scrubStyle(val)
      if (cleaned) el.setAttribute('style', cleaned)
      else el.removeAttribute('style')
      continue
    }
    // Defense-in-depth: reject any value smuggling a script/data URI.
    if (/(javascript|data|vbscript)\s*:/i.test(val)) {
      el.removeAttribute(attr.name)
    }
  }

  // Recurse into (a static snapshot of) children.
  for (const child of Array.from(el.children)) {
    cleanElement(child)
  }
}

/**
 * Return a sanitized copy of an untrusted HTML string, safe to persist and
 * to assign back into a contenteditable surface.
 */
export function sanitizeHtml(dirty: string): string {
  if (!dirty) return ''
  // SSR / non-DOM guard (tests, print workers): fall back to escaping.
  if (typeof document === 'undefined') {
    return dirty.replace(/[<>&]/g, (c) => (c === '<' ? '&lt;' : c === '>' ? '&gt;' : '&amp;'))
  }
  const tpl = document.createElement('template')
  tpl.innerHTML = dirty // parsed inert — no scripts run, no <img> loads fire
  for (const child of Array.from(tpl.content.children)) {
    cleanElement(child)
  }
  return tpl.innerHTML
}
