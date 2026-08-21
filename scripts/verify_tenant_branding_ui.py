"""Browser proof that per-office branding actually reaches the screen.

WHY THIS FILE EXISTS
====================
Forty-nine source-level tests said this feature worked while a real browser
showed a partner office's portal in Dogar's colours, Dogar's typeface and
Dogar's company name. Three separate reasons, none visible in source:

  1. The only call site was inside ``<RequireAuth>``, so nothing was branded
     until after a successful login -- and the login page is precisely where an
     office's identity matters most.
  2. ``--brand-font`` was set and read, and still lost: ``index.html`` ships
     ``<body class="font-sans">`` and Tailwind's ``.font-sans`` (0,1,0)
     outranks the ``body`` type selector (0,0,1). A pure CASCADE outcome, which
     no static assertion can see.
  3. The company NAME was hardcoded in four surfaces whose surrounding branding
     was already correct, so each looked fine in isolation.

So this harness measures COMPUTED style and RENDERED text, and it measures the
unauthenticated page as well as the app shell.

MEMORY DISCIPLINE (measured): the sandbox has 985 MB. An earlier version held
four Playwright contexts open at once and took the whole sandbox down, turning a
passing run into an unrecoverable hang. Contexts are therefore opened and closed
ONE AT A TIME, and screenshots -- which are evidence, not verification -- can
never fail the run.

Usage:  ./venv/bin/python scripts/verify_tenant_branding_ui.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

# 127.0.0.1, NOT localhost: localhost resolves to ::1 first and uvicorn binds
# IPv4 only, so "localhost" fails with ECONNREFUSED ::1:3000.
BASE = "http://127.0.0.1:3000"
REPO = Path(__file__).resolve().parents[1]
ROOT_NAME = "Dogar Trading Corporation"

# Each portal has its OWN user database (data/tenants/tenant_<slug>.db). The root
# admin does not exist inside a tenant, which is the data boundary working as
# designed, not a harness inconvenience.
#
# These are FIXTURES, created by scripts/provision_demo_tenants.py -- not local
# sediment. They used to exist only as these literals, satisfied by a tenant
# that happened to be sitting in this sandbox's untracked data/ directory. Since
# data/ is deliberately excluded from the release archive (it holds the
# operator's real records), a fresh checkout produced
#     FAIL: login at /t/acme returned 401
# which reads as "tenant authentication is broken" when the truth was "the
# fixture was never created". Run the provisioner first; see FIXTURE_HINT below.
TENANT_CREDS = {"/t/acme": ("acme-admin@example.com", "AcmeProbe#2026")}

FIXTURE_HINT = (
    "the /t/acme demo tenant is missing or has a different admin password. "
    "It is a fixture, not sample data: create it with\n"
    "    ./venv/bin/python scripts/provision_demo_tenants.py\n"
    "(verify without mutating: same command with --check)"
)

PROBE = """() => ({
    bodyFont: getComputedStyle(document.body).fontFamily,
    brandFont: getComputedStyle(document.documentElement)
        .getPropertyValue('--brand-font').trim(),
    brandSolid: getComputedStyle(document.documentElement)
        .getPropertyValue('--brand-solid').trim(),
    bootFont: (window.__TENANT_BOOT__ || {}).tenant
        ? (window.__TENANT_BOOT__.tenant.fontKey || '') : '(no tenant)',
    title: document.title,
    bodyText: document.body.innerText || '',
    onLogin: !!document.querySelector('input[type=password]'),

    // AUTH-PAGE SURFACES (2026-08-20). Added after a screenshot -- not a
    // failing assertion -- showed a partner office's login page rendering a
    // photograph of Dogar's own logo wall behind a Dogar-blue "Sign In"
    // button, while every check above was already green. getComputedStyle
    // RESOLVES var(), so these read the value the compositor actually paints:
    // a url(...) for the root portal and a linear-gradient(...) for a tenant.
    // Null on the app shell, where these elements do not exist.
    heroBg: (() => { const e = document.querySelector('.panel-left');
        return e ? getComputedStyle(e).backgroundImage : null })(),
    softBg: (() => { const e = document.querySelector('.panel-right');
        return e ? getComputedStyle(e).backgroundImage : null })(),
    signinBg: (() => { const e = document.querySelector('.btn-signin');
        return e ? getComputedStyle(e).backgroundImage : null })(),
    signinInk: (() => { const e = document.querySelector('.btn-signin');
        return e ? getComputedStyle(e).color : null })(),
    eyebrowInk: (() => { const e = document.querySelector('.auth-accent');
        return e ? getComputedStyle(e).color : null })(),
})"""

FAILURES: list[str] = []


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    FAILURES.append(msg)


def root_creds() -> tuple[str, str]:
    env = {}
    for line in (REPO / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env.get("DEFAULT_ADMIN_EMAIL", ""), env.get("DEFAULT_ADMIN_PASSWORD", "")


def visit(browser, url: str, base_path: str, *, login: bool, shot: str | None):
    """Measure one page in its own context, then close it.

    Returns the probe dict, or None if the login could not be established (which
    is reported as a failure rather than silently measuring a login page).
    """
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    try:
        if login:
            email, pw = TENANT_CREDS.get(base_path) or root_creds()
            r = ctx.request.post(f"{BASE}{base_path}/api/auth/login",
                                 data={"username": email, "password": pw})
            if r.status != 200:
                msg = f"login at {base_path or '/'} returned {r.status}"
                # A 401 at a TENANT path is overwhelmingly likely to be a
                # missing fixture rather than a broken product, so say so
                # instead of leaving the reader to conclude the auth layer
                # regressed. Still a hard failure -- just an actionable one.
                if r.status == 401 and base_path in TENANT_CREDS:
                    msg += f" -- {FIXTURE_HINT}"
                fail(msg)
                return None
        page = ctx.new_page()
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(1400)
        out = page.evaluate(PROBE)
        out["errors"] = errors
        if login and out["onLogin"]:
            fail(f"{url} landed on the login page; the session was not established")
        if shot:
            # Evidence only. A failed screenshot must never change the verdict.
            try:
                page.screenshot(path=shot)
            except Exception as exc:  # noqa: BLE001
                print(f"NOTE: could not capture {shot}: {type(exc).__name__}")
        return out
    finally:
        ctx.close()


def expected_login_401s(errors: list[str]) -> bool:
    """On an unauthenticated page AuthContext probes /users/me and
    /users/me/permissions in parallel; with no cookie both correctly 401 and the
    SPA renders the login form. Allowed BY SHAPE (all 401, at most two) rather
    than by muting console errors on login pages, so a 500 still fails."""
    return 1 <= len(errors) <= 2 and all("401" in e for e in errors)


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        try:
            dogar = visit(browser, f"{BASE}/dashboard", "",
                          login=True, shot="/tmp/brand_dogar_app.png")
            tenant = visit(browser, f"{BASE}/t/acme/dashboard", "/t/acme",
                           login=True, shot="/tmp/brand_tenant_app.png")
            dlogin = visit(browser, f"{BASE}/login", "",
                           login=False, shot="/tmp/brand_dogar_login.png")
            tlogin = visit(browser, f"{BASE}/t/acme/login", "/t/acme",
                           login=False, shot="/tmp/brand_tenant_login.png")
        finally:
            browser.close()

    if not all((dogar, tenant, dlogin, tlogin)):
        print("RESULT: FAIL (could not measure every page)")
        return 3

    for label, m in (("dogar-app", dogar), ("tenant-app", tenant),
                     ("dogar-login", dlogin), ("tenant-login", tlogin)):
        print(f"{label:14s} font={m['bodyFont'][:44]!r} accent={m['brandSolid']!r} "
              f"title={m['title']!r}")

    # 1. THE ROOT PORTAL IS UNCHANGED -- the brief's hard requirement.
    for label, m in (("app", dogar), ("login", dlogin)):
        if m["brandFont"]:
            fail(f"--brand-font is set on the Dogar {label} page ({m['brandFont']!r}); "
                 "the control portal is being themed by the tenant path")
        if "Inter" not in m["bodyFont"]:
            fail(f"Dogar {label} body font is {m['bodyFont']!r}, expected Inter")
        if m["brandSolid"] != "#1d4ed8":
            fail(f"Dogar {label} accent is {m['brandSolid']!r}, expected #1d4ed8")
        if ROOT_NAME not in m["title"]:
            fail(f"Dogar {label} tab title is {m['title']!r}; the root portal lost "
                 "its own identity to the tenant-aware path")

    # 2. THE OFFICE GETS ITS OWN TYPEFACE, ACCENT AND NAME -- on both page kinds,
    #    because the original defect was invisible on authenticated pages only.
    for label, m in (("app", tenant), ("login", tlogin)):
        if m["bootFont"] != "serif":
            fail(f"tenant {label} boot payload carries {m['bootFont']!r}, not 'serif'")
        if "Georgia" not in m["brandFont"]:
            fail(f"--brand-font on the tenant {label} page is {m['brandFont']!r}")
        if "Georgia" not in m["bodyFont"]:
            fail(f"the tenant {label} COMPUTED body font is {m['bodyFont']!r} -- the "
                 "token is set but nothing renders with it (outranked or unread)")
        if m["brandSolid"] == "#1d4ed8":
            fail(f"the tenant {label} page still shows the Dogar accent")
        if ROOT_NAME in m["bodyText"]:
            leaked = [ln.strip() for ln in m["bodyText"].splitlines() if ROOT_NAME in ln]
            fail(f"the tenant {label} page shows the ROOT company name: {leaked[:3]}")
        if ROOT_NAME in m["title"]:
            fail(f"the tenant {label} tab title is {m['title']!r}")
        if "Acme" not in m["title"]:
            fail(f"the tenant {label} tab title {m['title']!r} omits the office name")

    # 2b. THE LOGIN PAGE'S HERO ART AND PRIMARY BUTTON.
    #     These four defects were invisible to every check above: the office
    #     accent, typeface, name and tab title were all already correct on this
    #     exact page while it displayed Dogar's office photograph and a Dogar
    #     blue button. Measured on the DOGAR page too -- an over-eager fix that
    #     replaced the root portal's photographs with a gradient would be a
    #     branding regression on the flagship portal, and "the tenant looks
    #     right" would never have caught it.
    if "office-left.jpg" not in (dlogin["heroBg"] or ""):
        fail(f"the Dogar login hero is {dlogin['heroBg']!r}; the root portal's own "
             "photograph was replaced by the tenant theming path")
    if "office-right.jpg" not in (dlogin["softBg"] or ""):
        fail(f"the Dogar login right panel is {dlogin['softBg']!r}, not its photograph")

    if tlogin["heroBg"] is None:
        fail("no .panel-left on the tenant login page -- the hero could not be measured")
    elif "office-left.jpg" in tlogin["heroBg"]:
        fail("the tenant login page still shows DOGAR'S OFFICE PHOTOGRAPH as its "
             "full-height hero; --brand-hero is not reaching .panel-left")
    elif "gradient" not in tlogin["heroBg"]:
        fail(f"the tenant login hero is {tlogin['heroBg']!r}; expected an "
             "accent-derived gradient")

    if "office-right.jpg" in (tlogin["softBg"] or ""):
        fail("the tenant login RIGHT panel still shows Dogar's office photograph")

    if tlogin["signinBg"] is None:
        fail("no .btn-signin on the tenant login page -- the primary action could "
             "not be measured")
    else:
        # The accent is #ff0000 -> rgb(255, 0, 0) must appear in the gradient the
        # browser resolved. Asserting on the RESOLVED value, not on the presence
        # of a var() reference, is the whole point: the font token was present
        # and read and still lost to specificity.
        if "255, 0, 0" not in tlogin["signinBg"]:
            fail(f"the tenant's Sign In button paints {tlogin['signinBg']!r}, which "
                 "does not contain the office accent rgb(255, 0, 0)")
        if "rgb(29, 78, 216)" in tlogin["signinBg"] or "29, 78, 216" in tlogin["signinBg"]:
            fail("the tenant's Sign In button is still Dogar blue")
    if tlogin["eyebrowInk"] and "255, 0, 0" not in tlogin["eyebrowInk"]:
        fail(f"the tenant's 'Secure Sign In' chip is {tlogin['eyebrowInk']!r}, not the "
             "office accent")
    # The button label must stay legible on whatever accent the office chose.
    # readableOn() picks white or slate-900; on #ff0000 that is white.
    if tlogin["signinInk"] and tlogin["signinInk"] not in (
            "rgb(255, 255, 255)", "rgb(15, 23, 42)"):
        fail(f"the tenant Sign In label is {tlogin['signinInk']!r}, neither of the two "
             "WCAG-checked inks")

    # 3. The two portals must genuinely differ (guards against both being wrong
    #    in the same direction, which every "looks branded" check would pass).
    if dogar["bodyFont"] == tenant["bodyFont"]:
        fail("both portals render in the same typeface")
    if dogar["brandSolid"] == tenant["brandSolid"]:
        fail("both portals render with the same accent colour")

    # 4. Console hygiene.
    for label, m in (("dogar-app", dogar), ("tenant-app", tenant)):
        if m["errors"]:
            fail(f"console errors on {label}: {m['errors'][:4]}")
    for label, m in (("dogar-login", dlogin), ("tenant-login", tlogin)):
        if m["errors"] and not expected_login_401s(m["errors"]):
            fail(f"unexpected console errors on {label}: {m['errors'][:4]}")

    print("RESULT:", "PASS" if not FAILURES else f"FAIL ({len(FAILURES)})")
    return 0 if not FAILURES else 2


if __name__ == "__main__":
    sys.exit(main())
