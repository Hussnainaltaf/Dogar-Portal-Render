/** @type {import('tailwindcss').Config} */
// Brand tokens ported verbatim from app/templates/base.html (tailwind.config).
//
// The Tailwind utilities are now COMPILED INTO THE SPA BUNDLE (see the
// @tailwind directives in src/index.css) instead of the Play CDN — this
// removes the "cdn.tailwindcss.com should not be used in production" console
// warning.
//
// The blue→green theme switch is handled at runtime by toggling the
// `.theme-forest-green` class on <html>/<body>, which flips the `--blue-*`
// CSS variables defined in src/index.css. The `blue` palette below is mapped
// to those variables so every `*-blue-*` utility recolours automatically.
const withVar = (name) => `rgb(var(${name}) / <alpha-value>)`;

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    screens: {
      xs: '420px',
      sm: '640px',
      md: '768px',
      lg: '1024px',
      xl: '1280px',
      '2xl': '1536px',
    },
    extend: {
      colors: {
        brand: {
          primary: withVar('--blue-600'),
          'primary-dark': withVar('--blue-700'),
          'primary-light': withVar('--blue-100'),
          blue: withVar('--blue-600'),
          'blue-dark': withVar('--blue-700'),
          'blue-light': withVar('--blue-100'),
          green: '#16a34a',
          'green-dark': '#15803d',
          'green-light': '#dcfce7',
        },
        // WCAG AA fix, applied at the palette rather than at 192 call sites.
        // ------------------------------------------------------------------
        // Stock Tailwind `slate-400` is #94a3b8. Measured on the Roles cards,
        // 11px semibold `text-slate-400` label text ("All access",
        // "30 permissions") composited over the card glass at only 3.14:1 --
        // below the 4.5:1 AA floor for normal-size text. This is a PRE-EXISTING
        // defect, not a glass regression: `text-slate-400` appears 192 times
        // across the SPA and 26 times in MiscPages.tsx at the previous commit,
        // and it was invisible until the contrast probe stopped sampling a
        // single `.card` and started scoring every text node on the page.
        //
        // #64748b is stock `slate-500`, which clears AA on both white and glass
        // surfaces while staying a clearly secondary grey next to
        // `slate-600/700` body copy. Overriding the palette entry fixes every
        // occurrence at once and keeps future `text-slate-400` usage safe by
        // construction, which editing individual JSX files would not.
        // Re-verified by scripts/verify_glass_ui.py after this change.
        slate: {
          400: '#64748b',
        },
        // Virtualised blue palette -> driven by CSS variables (see src/index.css).
        blue: {
          50: withVar('--blue-50'),
          100: withVar('--blue-100'),
          200: withVar('--blue-200'),
          300: withVar('--blue-300'),
          400: withVar('--blue-400'),
          500: withVar('--blue-500'),
          600: withVar('--blue-600'),
          700: withVar('--blue-700'),
          800: withVar('--blue-800'),
          900: withVar('--blue-900'),
        },
      },
      fontFamily: {
        // MEASURED DEFECT 2026-08-19 (found in Chromium, not in source review).
        //
        // `--brand-font` was set correctly on <html>, and index.css had
        // `body { font-family: var(--brand-font, var(--font-body)) }`. The
        // computed body font was STILL Inter on a tenant portal, because
        // index.html ships `<body class="... font-sans ...">` and Tailwind
        // emits `.font-sans { font-family: Inter, system-ui, sans-serif }`.
        // A class selector is (0,1,0); the `body` type selector is (0,0,1).
        // The tenant rule lost on specificity, silently, with both
        // declarations present and neither one wrong on its own.
        //
        // Fixed at the THEME rather than by bumping the losing rule's
        // specificity or reaching for !important: `font-sans` is applied to
        // <body> and to individual components, and all of them should follow
        // the active office. One token substitution covers every occurrence
        // present and future, instead of a specificity arms race that the next
        // added utility class would restart.
        //
        // DOGAR IS UNCHANGED, structurally: the var() fallback chain is the
        // literal previous value, and `--brand-font` is never set on the root
        // portal (applyTenantBranding is a no-op without a tenant). So the
        // root portal computes 'Inter, system-ui, sans-serif' exactly as before
        // -- verified by the browser probe asserting that exact string.
        sans: ['var(--brand-font, Inter)', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'monospace'],
      },
    },
  },
  plugins: [],
}
