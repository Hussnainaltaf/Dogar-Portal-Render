/**
 * Tenant branding -> live CSS custom properties.
 *
 * WHY THIS MODULE EXISTS (2026-08-18)
 * ===================================
 * The backend already resolved per-tenant branding and injected it into
 * `window.__TENANT_BOOT__` (see app/main.py), and `TENANT_INFO.color` carried
 * each office's `primary_color`. Nothing applied it. A grep for
 * `TENANT_INFO?.color` across src/ returned NOTHING: the Sidebar read `name`,
 * `shortName` and `logo`, the Login page read `name` and `subtitle`, and the
 * colour was fetched on every page load and then dropped on the floor. Every
 * tenant portal rendered in Dogar blue no matter what the admin configured.
 *
 * The Topbar was worse: it hard-coded the literal string "Dogar", so a partner
 * office's staff saw another company's name in their own portal chrome.
 *
 * WHAT THIS DOES
 * --------------
 * `applyTenantBranding()` writes the tenant accent into the SAME four brand
 * tokens that index.css already funnels every accent surface through
 * (--brand-solid, --brand-solid-hover, --brand-tint, --brand-rule) plus
 * --brand-on-solid. Nothing downstream needs to change: .brand-fill,
 * .brand-tile, .nav-active, .dtc-head and .app-band all read those tokens, so
 * one write re-skins the whole shell with zero re-render.
 *
 * THE PART THAT MATTERS MOST: CONTRAST IS NOT NEGOTIABLE
 * -----------------------------------------------------
 * A tenant admin can type any hex into the branding form, including #ffff00 or
 * #f5f5f5. Those are legal colours and catastrophic accent surfaces: white text
 * on yellow is roughly 1.07:1, far below the WCAG AA 4.5:1 floor, which makes
 * primary buttons and the active nav item literally unreadable.
 *
 * So the foreground is CHOSEN, not assumed: `readableOn()` measures relative
 * luminance per WCAG 2.1 and returns whichever of white/near-black actually
 * clears the threshold. The accent itself is never silently rewritten -- the
 * office keeps the colour it asked for -- but the text placed on it is always
 * legible. Refusing to render the tenant's colour would be overriding their
 * branding; making the label invisible would be shipping a broken portal.
 * Picking the readable foreground is the only option that does neither.
 *
 * All of the maths below is pure and exported so it is directly testable
 * without a browser. See tests/test_tenant_branding_contract.py, which parses
 * this file, and the unit expectations encoded there.
 */

import type { TenantInfo } from './api'

/** The Dogar defaults. MUST match the `:root` values in index.css. */
export const DEFAULT_BRAND = {
  solid: '#1d4ed8',
  hover: '#1a44bd',
  tint: '#eff4ff',
  rule: '#dbe4f5',
  onSolid: '#ffffff',
} as const

/** WCAG AA minimum contrast for normal-size body text. */
export const AA_CONTRAST = 4.5

export interface Rgb { r: number; g: number; b: number }

/**
 * Parse `#rgb` / `#rrggbb` (with or without the hash) into channels.
 *
 * Returns null for anything unparseable rather than throwing or guessing.
 * A tenant row can hold legacy junk ("blue", "", a CSS variable), and a
 * branding routine that throws would take the whole shell down over a
 * cosmetic field.
 */
export function parseHex(input: string | null | undefined): Rgb | null {
  if (!input) return null
  const s = String(input).trim().replace(/^#/, '')
  if (/^[0-9a-fA-F]{3}$/.test(s)) {
    return {
      r: parseInt(s[0] + s[0], 16),
      g: parseInt(s[1] + s[1], 16),
      b: parseInt(s[2] + s[2], 16),
    }
  }
  if (/^[0-9a-fA-F]{6}$/.test(s)) {
    return {
      r: parseInt(s.slice(0, 2), 16),
      g: parseInt(s.slice(2, 4), 16),
      b: parseInt(s.slice(4, 6), 16),
    }
  }
  return null
}

export const toHex = ({ r, g, b }: Rgb): string =>
  '#' + [r, g, b].map(v => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0')).join('')

/** WCAG 2.1 relative luminance. */
export function relativeLuminance({ r, g, b }: Rgb): number {
  const chan = (v: number) => {
    const c = v / 255
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
  }
  return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)
}

/** WCAG 2.1 contrast ratio between two colours (order-independent, >= 1). */
export function contrastRatio(a: Rgb, b: Rgb): number {
  const la = relativeLuminance(a)
  const lb = relativeLuminance(b)
  const [hi, lo] = la >= lb ? [la, lb] : [lb, la]
  return (hi + 0.05) / (lo + 0.05)
}

/**
 * The readable foreground for a given accent surface.
 *
 * Picks whichever of white / near-black has the HIGHER contrast, so a pale
 * tenant accent (#f5f5f5, #ffff00) gets dark text instead of invisible white.
 * Near-black rather than pure black because #0f172a is the slate the rest of
 * the UI uses for text, and it still clears AA comfortably.
 */
export function readableOn(surface: Rgb): string {
  const white: Rgb = { r: 255, g: 255, b: 255 }
  const ink: Rgb = { r: 15, g: 23, b: 42 }        // slate-900, matches the shell
  return contrastRatio(surface, white) >= contrastRatio(surface, ink)
    ? '#ffffff'
    : '#0f172a'
}

const mix = (a: Rgb, b: Rgb, t: number): Rgb => ({
  r: a.r + (b.r - a.r) * t,
  g: a.g + (b.g - a.g) * t,
  b: a.b + (b.b - a.b) * t,
})

const BLACK: Rgb = { r: 0, g: 0, b: 0 }
const WHITE: Rgb = { r: 255, g: 255, b: 255 }

export interface BrandPalette {
  solid: string
  hover: string
  tint: string
  rule: string
  onSolid: string
  /** Contrast of `onSolid` against `solid`. Exposed so tests and the UI can
   *  assert legibility instead of trusting that it worked. */
  contrast: number
}

/**
 * Derive the full accent palette from one tenant colour.
 *
 * The shades are mixes toward black/white rather than HSL rotations, because a
 * mix keeps the hue exactly as the office chose it. `tint` and `rule` are used
 * behind text (selected rows, hairlines), so they stay very close to white
 * regardless of how dark the accent is -- otherwise a navy tenant would get a
 * near-black selected-row wash with unreadable content on it.
 */
export function derivePalette(color: string | null | undefined): BrandPalette {
  const base = parseHex(color)
  if (!base) {
    return {
      ...DEFAULT_BRAND,
      contrast: contrastRatio(parseHex(DEFAULT_BRAND.solid)!, WHITE),
    }
  }
  const onSolid = readableOn(base)
  return {
    solid: toHex(base),
    hover: toHex(mix(base, BLACK, 0.12)),
    tint: toHex(mix(base, WHITE, 0.92)),
    rule: toHex(mix(base, WHITE, 0.78)),
    onSolid,
    contrast: contrastRatio(base, parseHex(onSolid)!),
  }
}

/* ─────────────────────────────────────────────────────────────────────────────
   HERO ART FOR THE UNAUTHENTICATED PAGES
   ======================================
   MEASURED DEFECT 2026-08-20, found in a SCREENSHOT of /t/acme/login and in
   nothing else: every assertion in the branding harness had already passed
   (typeface, accent token, tab title, rendered company name), and the page
   still showed a full-height photograph of DOGAR'S OWN LOGO WALL
   (`/static/img/login/office-left.jpg`, hard-coded in index.css) to a partner
   office's staff on the very first screen they ever see. The right-hand panel
   carried a second photo of the same office, blurred but still Dogar's.

   WHY A DERIVED GRADIENT AND NOT "THE TENANT'S OWN PHOTO"
   ------------------------------------------------------
   A tenant row has a `logo_filename`; it has no hero photograph, and there is
   no upload field for one. The three honest options were:
     1. keep showing Dogar's office            -> the bug. Rejected.
     2. stretch the tenant's LOGO to full-bleed -> a 200px-wide PNG scaled to
        half a 1080p viewport is a blurred smear, and `cover` would crop it.
     3. paint a surface derived from the accent the office actually chose.
   (3) is the only one that is both tenant-specific and guaranteed to render
   well at any viewport, so that is what these two functions build. The office
   logo is still shown, at its natural size, in the `.brand-badge` lockup that
   sits on top of the panel -- so identity is not lost, it is just no longer
   somebody else's photograph.

   DOGAR IS UNCHANGED, STRUCTURALLY, NOT BY CONVENTION
   --------------------------------------------------
   These tokens are only ever WRITTEN when a tenant is resolved (the same
   `if (!root || !tenant) return null` guard that gates the whole function).
   The root portal never sets them, so index.css's `var(--brand-hero, url(...))`
   falls through to the literal previous value and the Dogar login page is
   byte-for-byte the page it was before. That is a property of the cascade, not
   a promise in a comment: deleting the fallback is mutation BW.
   ───────────────────────────────────────────────────────────────────────── */

/**
 * The full-bleed left-hand hero surface for a tenant's auth pages.
 *
 * Deep enough that the translucent white `.brand-badge` lockup and its
 * slate-800 text keep their contrast, which is why it runs solid -> hover
 * (a darker mix of the same hue) rather than out toward the pale tint.
 */
export function heroBackground(palette: BrandPalette): string {
  return `linear-gradient(140deg, ${palette.solid} 0%, ${palette.hover} 62%, ${palette.solid} 100%)`
}

/**
 * The right-hand panel, which sits UNDER the sign-in card.
 *
 * Must stay light: the card is `rgba(255,255,255,.96)` and every label on it is
 * slate. A dark accent here would read as a floating white box on a coloured
 * field and would fight the glass treatment. Runs tint -> white -> rule, so a
 * navy office and a yellow office both get a pale wash of their own hue.
 */
export function heroSoftBackground(palette: BrandPalette): string {
  return `linear-gradient(165deg, ${palette.tint} 0%, #ffffff 58%, ${palette.rule} 100%)`
}

/**
 * Font stack for the tenant, if one is configured.
 *
 * Deliberately an ALLOW-LIST rather than passing the stored string through.
 * `font-family` is injected into a live stylesheet property, and while CSS
 * cannot execute script, an unvalidated value is still an injection surface
 * (`}` terminating the rule) and a trivial way for a bad row to break the
 * entire shell's typography. Unknown values fall back to the system stack.
 */
export const FONT_STACKS: Record<string, string> = {
  system: 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
  inter: 'Inter, -apple-system, BlinkMacSystemFont, sans-serif',
  roboto: 'Roboto, -apple-system, BlinkMacSystemFont, sans-serif',
  lato: 'Lato, -apple-system, BlinkMacSystemFont, sans-serif',
  serif: 'Georgia, "Times New Roman", serif',
  mono: '"SF Mono", ui-monospace, Menlo, Consolas, monospace',
}

export function resolveFontStack(key: string | null | undefined): string {
  const k = String(key || '').trim().toLowerCase()
  return FONT_STACKS[k] || FONT_STACKS.system
}

/** The brand text shown in the topbar / sidebar for the active tenant. */
export function brandLabel(tenant: TenantInfo | null | undefined): string {
  return (tenant?.shortName || tenant?.name || 'Dogar').trim() || 'Dogar'
}

/** The full company name, used for tooltips and the document letterhead. */
export function brandFullName(tenant: TenantInfo | null | undefined): string {
  return (tenant?.name || 'Dogar Trading Corporation').trim() || 'Dogar Trading Corporation'
}

/**
 * Write the tenant palette into the document's custom properties.
 *
 * Idempotent and safe to call on every mount. When no tenant is resolved (the
 * Dogar root portal) this is a NO-OP by design: the Dogar branding stays
 * exactly as authored in index.css, which is a hard requirement -- the root
 * portal's identity must never be altered by the tenant theming path.
 */
export function applyTenantBranding(
  tenant: TenantInfo | null | undefined,
  root: HTMLElement | null = typeof document !== 'undefined' ? document.documentElement : null,
): BrandPalette | null {
  if (!root || !tenant) return null
  const palette = derivePalette(tenant.color)
  root.style.setProperty('--brand-solid', palette.solid)
  root.style.setProperty('--brand-solid-hover', palette.hover)
  root.style.setProperty('--brand-tint', palette.tint)
  root.style.setProperty('--brand-rule', palette.rule)
  root.style.setProperty('--brand-on-solid', palette.onSolid)
  // No `as any` cast: `fontKey` is now a declared member of TenantInfo, so the
  // compiler checks this. The cast used to be load-bearing camouflage -- it let
  // the boot payload omit fontKey entirely with no build error, which is how
  // the font half of the branding request silently did nothing.
  const font = resolveFontStack(tenant.fontKey)
  root.style.setProperty('--brand-font', font)
  // The auth-page hero surfaces. See the long note above heroBackground():
  // written ONLY on a resolved tenant, so the root portal's var() fallbacks in
  // index.css keep Dogar's photographs exactly as authored.
  root.style.setProperty('--brand-hero', heroBackground(palette))
  root.style.setProperty('--brand-hero-soft', heroSoftBackground(palette))
  return palette
}
