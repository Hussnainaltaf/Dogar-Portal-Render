#!/usr/bin/env python3
"""Verify the light-glassmorphism layer (app.css v19) IN A REAL BROWSER.

WHY THIS SCRIPT EXISTS
----------------------
A stylesheet is not "applied" because the file contains the rules. It is
applied when the cascade resolves and the compositor paints it. Between those
two facts sit the `?v=` cache bust, source order against the v18 flatten layer,
`@supports` gating, and the browser's own capability checks -- and in this
project a visual change has already shipped invisible before.

`tests/test_glass_design_system.py` checks the SOURCE contract statically and
is mutation-proven (9 mutations caught, 1 control survived). This script covers
what static analysis fundamentally cannot: it asks the layout engine what it
actually computed, on real pages, after a real login.

WHAT IT ASSERTS, PER PAGE
  1. the frosted surfaces really resolve a non-none backdrop-filter
  2. .card / .stat-tile are really TRANSLUCENT (alpha < 1), not opaque white
  3. the ambient canvas is really painted on <body> (glass needs something to
     refract, or the blur is invisible and surfaces just look pale)
  4. body / #root / .spa-main-content / .page-enter did NOT become containing
     blocks -- the 2026-08-13 dialog-clipping outage, re-checked at RUNTIME
     rather than by reading the CSS
  5. text on glass still clears the WCAG AA 4.5:1 contrast floor, computed by
     compositing the translucent surface over the canvas behind it
  6. no console errors

It also writes a screenshot per page to /tmp/glass_<label>.png so the result is
visually reviewable rather than merely asserted.

USAGE
    PORTAL_URL=https://<host> ./venv/bin/python scripts/verify_glass_ui.py

USE AN HTTPS URL: the access_token cookie is issued secure=True, so a plain
http:// origin silently drops it and every request 401s.

Credentials come from $ADMIN_EMAIL / $ADMIN_PW, falling back to
DEFAULT_ADMIN_EMAIL / DEFAULT_ADMIN_PASSWORD in .env. Never from source.

EXIT STATUS
    0 = every assertion held
    2 = a real defect was measured
    4 = harness problem (no playwright, login failed)
"""
from __future__ import annotations

import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Dashboard (stat tiles), list pages (data tables + toolbars), admin pages.
PAGES = [
    ("dashboard", "/"),
    ("clients", "/clients"),
    ("candidates", "/candidates"),
    ("demands", "/demands"),
    ("users", "/users"),
    ("roles", "/roles"),
    # Added 2026-08-20. The Audit Log page was built AFTER this harness last
    # ran, so the newest screen in the portal was the only one whose glass
    # surfaces had never been measured in a browser -- exactly the page most
    # likely to have been hand-styled rather than built from the design system.
    ("audit-log", "/admin/audit-log"),
]

# The glass surface classes that carry text worth scoring for contrast.
#
# Defined ONCE, in Python, and injected into BOTH the measurement probe and the
# readiness wait. It previously lived only inside the probe's JS, and when the
# readiness wait was strengthened to check for scoreable text it needed the same
# list -- so a second copy appeared. Two hand-maintained copies of the same
# selector list is precisely how a page gets a new surface class added to the
# probe, missed in the wait, and starts racing again months later with no
# obvious cause. One definition, one source of drift eliminated.
SURFACE_SELECTORS = [".card", ".stat-tile", ".data-table", ".page-header",
                     ".toolbar", ".filter-bar"]
_SURF_JS = "[" + ", ".join(f"'{s}'" for s in SURFACE_SELECTORS) + "]"

# Runs inside the page; must return JSON-serialisable data only.
PROBE = r"""
() => {
  const val = (el, prop) => getComputedStyle(el).getPropertyValue(prop);
  const alphaOf = (rgb) => {
    const m = /rgba?\(([^)]+)\)/.exec(rgb || '');
    if (!m) return 1;
    const parts = m[1].split(',').map(s => parseFloat(s));
    return parts.length > 3 ? parts[3] : 1;
  };
  const lum = (rgb) => {
    const m = /rgba?\(([^)]+)\)/.exec(rgb || '');
    if (!m) return null;
    const p = m[1].split(',').map(s => parseFloat(s));
    const f = c => { c = c / 255; return c <= 0.03928 ? c/12.92 : Math.pow((c+0.055)/1.055, 2.4); };
    return 0.2126*f(p[0]) + 0.7152*f(p[1]) + 0.0722*f(p[2]);
  };
  const bfOf = (el) => {
    const a = (val(el, 'backdrop-filter') || '').trim();
    const b = (val(el, '-webkit-backdrop-filter') || '').trim();
    return (a && a !== 'none') ? a : b;
  };

  const out = { surfaces: [], cb: {}, canvas: {}, contrast: null };

  for (const sel of ['.card', '.stat-tile', 'header.topbar', 'aside#sidebar',
                     '.form-input', '.data-table thead th', '.page-header']) {
    const el = document.querySelector(sel);
    if (!el) { out.surfaces.push({ sel, present: false }); continue; }
    const bg = val(el, 'background-color');
    out.surfaces.push({
      sel, present: true,
      bf: bfOf(el),
      bg: (bg || '').trim(),
      alpha: alphaOf(bg),
    });
  }

  out.canvas.bgImage = (val(document.body, 'background-image') || '').slice(0, 140);
  out.canvas.bgColor = val(document.body, 'background-color');

  // THE OUTAGE CHECK: any of these makes fixed overlays resolve against the
  // element instead of the viewport.
  for (const [name, sel] of [['body','body'], ['root','#root'],
                             ['main','.spa-main-content'], ['pageEnter','.page-enter']]) {
    const el = document.querySelector(sel);
    if (!el) { out.cb[name] = null; continue; }
    out.cb[name] = {
      transform: val(el,'transform'),
      filter: val(el,'filter'),
      'backdrop-filter': bfOf(el),
      perspective: val(el,'perspective'),
      'will-change': val(el,'will-change'),
      contain: val(el,'contain'),
    };
  }

  // Contrast: composite each translucent surface over the canvas behind it and
  // keep the WORST result on the page.
  //
  // This used to look only for `.card`, so any page whose glass is carried by
  // `.stat-tile` (the dashboard) or a bare table reported `contrast: null` --
  // and null was then treated as acceptable. Five of six pages were measuring
  // nothing while the run still printed PASS, which is exactly the kind of
  // vacuous check that makes a suite look stronger than it is. Now every glass
  // surface class is sampled, the minimum is reported, and a page where nothing
  // could be measured is a hard failure rather than a silent pass.
  const SURF = __SURF__;
  const bl = lum(getComputedStyle(document.body).backgroundColor);

  // Composite the text element's OWN background stack, walking outward until an
  // opaque layer is reached, and only then fall back to the page canvas.
  //
  // A first version composited the text against the alpha of the *container*
  // it was found in, which produced a false failure: `.data-table` is
  // deliberately transparent (alpha 0) in the v19 layer so the card's glass
  // shows through, so a `<th>` was scored against the page canvas -- 4.05:1,
  // "below AA". Measuring the real stack showed the `th` paints its own opaque
  // rgb(248,250,252), i.e. the text was never on the canvas at all. Reporting a
  // sticky table header as an accessibility violation would have sent me
  // "fixing" a colour that was already correct.
  const effBg = (el) => {
    let acc = null, accA = 0;   // accumulated colour, front-to-back
    let node = el;
    while (node && node.nodeType === 1) {
      const c = getComputedStyle(node).backgroundColor;
      const a = alphaOf(c), l = lum(c);
      if (l !== null && a > 0) {
        if (acc === null) { acc = l; accA = a; }
        else { acc = acc + (1 - accA) * a * l; accA = accA + (1 - accA) * a; }
        if (accA >= 0.999) return acc;      // fully opaque: stack resolved
      }
      node = node.parentElement;
    }
    if (bl === null) return acc;
    return acc === null ? bl : acc + (1 - accA) * bl;
  };

  let worst = null, measured = 0;
  const seen = new Set();
  for (const sel of SURF) {
    for (const surf of document.querySelectorAll(sel)) {
      for (const probe of [surf, ...surf.querySelectorAll(
             'td, th, p, span, a, h1, h2, h3')].slice(0, 12)) {
        const text = (probe.textContent || '').trim();
        if (!text) continue;                 // no text => no contrast to score
        if (seen.has(probe)) continue;
        seen.add(probe);
        const fg = lum(getComputedStyle(probe).color);
        const eff = effBg(probe);
        if (fg === null || eff === null) continue;
        const hi = Math.max(fg, eff), lo = Math.min(fg, eff);
        const ratio = (hi + 0.05) / (lo + 0.05);
        measured++;
        if (worst === null || ratio < worst) worst = ratio;
      }
    }
  }
  out.contrast = worst === null ? null : Number(worst.toFixed(2));
  out.contrastSamples = measured;
  return out;
}
""".replace("__SURF__", _SURF_JS)

# The readiness predicate for the contrast measurement, expressed as the
# measurement's own precondition: at least one LAID-OUT glass surface (non-zero
# box) containing at least one element with non-empty text. It shares
# SURFACE_SELECTORS and the same descendant list as PROBE, so the thing waited
# for and the thing measured cannot disagree.
MEASURABLE = r"""
() => {
  const SURF = __SURF__;
  for (const sel of SURF) {
    for (const s of document.querySelectorAll(sel)) {
      const r = s.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) continue;   // attached but not laid out
      for (const p of [s, ...s.querySelectorAll(
             'td, th, p, span, a, h1, h2, h3')]) {
        if ((p.textContent || '').trim()) return true;
      }
    }
  }
  return false;
}
""".replace("__SURF__", _SURF_JS)

# Values that mean "this property is not set", so are safe.
INERT = {"none", "auto", "normal", "0px", ""}


def _env_credentials() -> tuple[str | None, str | None]:
    email = os.environ.get("ADMIN_EMAIL")
    pw = os.environ.get("ADMIN_PW")
    if email and pw:
        return email, pw
    path = os.path.join(REPO, ".env")
    if os.path.exists(path):
        vals = {}
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*)$", line)
                if m:
                    vals[m.group(1)] = m.group(2).strip().strip("'\"")
        return (email or vals.get("DEFAULT_ADMIN_EMAIL"),
                pw or vals.get("DEFAULT_ADMIN_PASSWORD"))
    return email, pw


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed: ./venv/bin/pip install playwright "
              "&& ./venv/bin/python -m playwright install chromium")
        return 4

    base = (sys.argv[1] if len(sys.argv) > 1
            else os.environ.get("PORTAL_URL", "https://localhost:3000")).rstrip("/")
    email, pw = _env_credentials()
    if not email or not pw:
        print("no admin credentials: set $ADMIN_EMAIL and $ADMIN_PW, or provide "
              "DEFAULT_ADMIN_EMAIL / DEFAULT_ADMIN_PASSWORD in .env")
        return 4

    vw, vh = 1440, 900
    if os.environ.get("VIEWPORT"):
        m = re.match(r"(\d+)x(\d+)$", os.environ["VIEWPORT"])
        if m:
            vw, vh = int(m.group(1)), int(m.group(2))

    print(f"base={base}  viewport={vw}x{vh}  admin={email}")

    problems: list[str] = []
    notes: list[str] = []
    console_errors: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = browser.new_context(viewport={"width": vw, "height": vh},
                                  ignore_https_errors=True)
        # The login body key is `username`, sent as JSON.
        resp = ctx.request.post(f"{base}/api/auth/login",
                                data={"username": email, "password": pw})
        if not resp.ok:
            print(f"login failed: HTTP {resp.status} {resp.text()[:200]}")
            print("if this is a 401 over plain http://, the secure access_token "
                  "cookie was dropped -- pass the HTTPS URL")
            ctx.close(); browser.close()
            return 4

        shot_failures: list[str] = []
        page = ctx.new_page()
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)

        # ONLY_PAGES lets the harness be run in chunks, e.g.
        #     ONLY_PAGES=audit-log PORTAL_URL=http://127.0.0.1:3000 ...
        # This exists because the sandbox has a hard ~985MB ceiling: driving all
        # six pages through one Chromium context exhausts it partway and
        # Page.screenshot times out at 30s. That timeout is an ENVIRONMENT
        # limit, not a page defect -- but "the harness crashed" and "the page is
        # broken" must never be allowed to look the same, so the fix is to make
        # the run divisible rather than to widen the timeout and hope.
        _only = [x.strip() for x in os.environ.get("ONLY_PAGES", "").split(",") if x.strip()]
        selected = [(lb, rt) for lb, rt in PAGES if not _only or lb in _only]
        if _only:
            missing = set(_only) - {lb for lb, _ in PAGES}
            if missing:
                print(f"FAIL: ONLY_PAGES names unknown page(s): {sorted(missing)}")
                ctx.close(); browser.close()
                return 4
        for label, route in selected:
            # `domcontentloaded`, not `networkidle`: several pages poll for live
            # activity, so networkidle never settles.
            page.goto(f"{base}{route}", wait_until="domcontentloaded")
            # Wait until the page is MEASURABLE, not merely until a surface is
            # attached.
            #
            # The previous version waited for `.card, .stat-tile, .data-table`
            # with state="attached" plus a flat 1200ms. That is a weaker
            # condition than it looks, and it produced a false accusation
            # against the /users page: `Users.tsx` renders its `.card` shell and
            # its six `<th>` as STATIC JSX, with only the tbody depending on the
            # fetch. So "attached" was satisfied on the first frame, the 1200ms
            # elapsed while the SPA was still hydrating under memory pressure,
            # and the probe scored 0 samples. The 0-sample vacuity guard then
            # correctly refused to report a vacuous PASS -- and the run blamed
            # the design system for a harness timing bug. Driving the same page
            # by hand with a 6000ms wait measures 4.55:1 over 13 samples.
            #
            # Widening the flat delay would only move the race. The fix is to
            # wait for the exact condition the probe requires: a glass surface
            # that CONTAINS SCOREABLE TEXT and has been laid out (non-zero box).
            # A wait derived from the measurement it guards cannot drift out of
            # agreement with it the way a hand-picked delay does.
            try:
                page.wait_for_function(MEASURABLE, timeout=20000)
            except Exception:  # noqa: BLE001
                # Deliberately swallowed: if the page never becomes measurable
                # the probe returns 0 samples and the vacuity guard below turns
                # that into a hard, explicit FAIL naming the page. Raising here
                # would abort the whole run on one page instead.
                pass
            # A small settle after the condition is met, so late-arriving rows
            # are included in the sample rather than the very first paint only.
            page.wait_for_timeout(600)
            r = page.evaluate(PROBE)
            # The screenshot is a DIAGNOSTIC, not an assertion: every check
            # below reads `r`, which has already been measured by this point.
            # It is therefore not allowed to decide the verdict. On this
            # sandbox (985MB ceiling) Page.screenshot intermittently exceeds
            # its 30s timeout even with fonts reported loaded, and an
            # unhandled timeout here aborted the whole run with a traceback --
            # so a page whose glass surfaces were measured PERFECTLY WELL
            # looked exactly like a page that had failed. Conflating "the
            # harness could not take a picture" with "the design system is
            # broken" is precisely the kind of false signal that gets a real
            # defect dismissed as flakiness later.
            try:
                page.screenshot(path=f"/tmp/glass_{label}.png", full_page=False,
                                timeout=15000)
            except Exception as exc:  # noqa: BLE001
                shot_failures.append(f"{label}: {type(exc).__name__}")

            present = [s for s in r["surfaces"] if s["present"]]
            frosted = [s for s in present if s["bf"] and s["bf"] != "none"]
            if not frosted:
                problems.append(
                    f"{label}: NO surface resolved a backdrop-filter -- the glass "
                    "layer is not being applied at all (cache bust? @supports? "
                    "source order?)")
            for s in present:
                if not s["bf"] or s["bf"] == "none":
                    notes.append(f"{label}: {s['sel']} not frosted")
                if s["sel"] in (".card", ".stat-tile") and s["alpha"] >= 1:
                    problems.append(
                        f"{label}: {s['sel']} is fully opaque (alpha={s['alpha']}) "
                        "-- translucency was the explicit requirement")

            if "gradient" not in (r["canvas"]["bgImage"] or ""):
                problems.append(
                    f"{label}: <body> has no gradient canvas "
                    f"({r['canvas']['bgImage'] or 'none'!r}) -- the glass has "
                    "nothing to refract and reads as flat pale grey")

            for name, cb in (r["cb"] or {}).items():
                if not cb:
                    continue
                for prop, v in cb.items():
                    if (v or "").strip() not in INERT:
                        problems.append(
                            f"{label}: {name} has {prop}:{v} -- establishes a "
                            "containing block, so `fixed inset-0` overlays are "
                            "CLIPPED to it instead of the viewport (the "
                            "2026-08-13 dialog outage)")

            c = r["contrast"]
            samples = r.get("contrastSamples", 0)
            if c is None:
                # An unmeasurable page is NOT a passing page. Previously this
                # branch fell through to the `notes` line and printed
                # "contrast None:1" while still counting as success.
                problems.append(
                    f"{label}: contrast could not be measured on any glass "
                    f"surface ({samples} samples) -- the check is vacuous here, "
                    f"so AA compliance on this page is UNVERIFIED. Either the "
                    f"page carries its glass on a class missing from "
                    f"SURFACE_SELECTORS, or it never became measurable within "
                    f"the readiness wait (see MEASURABLE).")
            elif c < 4.5:
                problems.append(
                    f"{label}: worst text-on-glass contrast {c}:1 is below the "
                    f"WCAG AA 4.5:1 floor across {samples} surfaces -- "
                    f"translucency was tuned too far")
            else:
                notes.append(
                    f"{label}: worst contrast {c}:1 over {samples} surfaces, "
                    f"frosted {len(frosted)}/{len(present)}, canvas painted")

        ctx.close()
        browser.close()

    real = [e for e in console_errors
            if not re.search(r"favicon|React DevTools|ERR_ABORTED", e)]
    if real:
        problems.append("console errors: " + " | ".join(real[:5]))

    print("\n=== GLASS UI VERIFICATION ===")
    for n in notes:
        print("  note  " + n)
    # Reported explicitly rather than swallowed. A skipped screenshot does not
    # change the verdict -- every assertion is made against the in-page probe --
    # but it does mean there is no picture to review for that page, and an
    # unreviewable page must never be silently indistinguishable from a
    # reviewed one.
    for sf in shot_failures:
        print(f"  note  screenshot not captured ({sf}) -- sandbox memory "
              f"limit; assertions for this page were still measured")
    if problems:
        print()
        for pr in problems:
            print("  FAIL  " + pr)
        print(f"\nRESULT: FAIL -- {len(problems)} problem(s)")
        return 2
    print("\nRESULT: PASS -- glass surfaces resolve translucent + frosted, ambient "
          "canvas painted, no containing-block regression, contrast >= 4.5:1, "
          "no console errors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
