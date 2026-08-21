#!/usr/bin/env python3
"""Measure every dialog in a real browser and prove its actions are reachable.

WHY THIS SCRIPT EXISTS
----------------------
On 2026-08-13 the operator reported, with screenshots, that the "Customize
Role" dialog rendered cut off mid-form: Role Name and Description were visible,
everything below them was gone, and the Save button could not be reached at
all. "Provision New Portal" was broken the same way. Both dialogs were
completely unusable -- while 656 automated tests were green.

They were green because **nothing in the suite had ever opened a dialog.**
Backend tests cannot see CSS; the frontend tests assert on source text. The
defect only existed once a browser resolved the cascade, so it was invisible to
every check the project had. (Root cause: `.page-enter` declared
`animation-fill-mode: both` plus a static `will-change`, permanently making the
routed content wrapper a containing block, so `fixed inset-0` overlays that were
not portalled to <body> were clipped to that wrapper instead of the viewport.
Full write-up in PROJECT_DOCUMENTATION.md section 14.3.)

`tests/test_fixed_overlay_containing_block.py` now pins the known *causes*
statically, which is cheap and catches a regression of this exact bug. This
script covers what static analysis fundamentally cannot: it opens each dialog at
a deliberately small viewport and asks the layout engine where things actually
ended up.

TWO DISTINCT VERDICTS -- THE DISTINCTION MATTERS
------------------------------------------------
  CLIPPED    a control is off-screen and NO ancestor can scroll to it.
             The dialog is unusable. This is the reported outage.
  BELOWFOLD  a control is off-screen but scrolling reaches it. The dialog
             works; the UX is poor.

Conflating these is a real hazard: the first version of this sweep flagged
"Create Demand File" as broken when its card scrolled perfectly well. Reporting
that as an outage would have been a false claim. Only CLIPPED fails the run;
BELOWFOLD is reported and returns a distinct exit status so it can be triaged
without blocking.

USAGE
-----
    ./venv/bin/python scripts/verify_dialog_geometry.py [BASE_URL]

BASE_URL defaults to $PORTAL_URL, else https://localhost:3000.

IMPORTANT -- USE AN HTTPS URL. The ``access_token`` cookie is issued with
secure=True in production mode, so a plain-HTTP localhost origin silently drops
it and every request 401s. In the sandbox, pass the HTTPS service URL.

Credentials come from $ADMIN_EMAIL / $ADMIN_PW, falling back to
DEFAULT_ADMIN_EMAIL / DEFAULT_ADMIN_PASSWORD in .env.

The viewport is 1024x640 by default: a 13" laptop with browser chrome, i.e. the
smallest ordinary desktop, not a contrived edge case. Override with $VIEWPORT
as WIDTHxHEIGHT.

EXIT STATUS
-----------
0 = no dialog is clipped and none needs scrolling to reach its actions
2 = at least one dialog is CLIPPED (unusable) -- a real defect
3 = no clipping, but some actions sit below the fold (usable, unpinned)
4 = harness problem (login failed, no dialogs could be opened at all)
"""
from __future__ import annotations

import json
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Dialogs to exercise: (label, route, text on the button that opens it).
# `{demand}` / `{client}` are substituted with real ids discovered from the DB,
# so this keeps working after the sample data changes.
CASES: list[tuple[str, str, str]] = [
    ("Roles / Customize Role", "/roles", "Customize"),
    ("Portal Admin / Provision", "/portal-admin", "Provision New Portal"),
    ("Users / Add User", "/users", "Add User"),
    ("Depositors / Add New", "/depositors", "Add New"),
    ("Visa Categories / Add New", "/visa-categories", "Add New"),
    ("Embassies / Add New", "/embassies", "Add New"),
    ("Cities / Add New", "/cities", "Add New"),
    ("Demands / Create Demand File", "/demands", "Add Demand File"),
    ("DemandDetail / Edit Demand File", "/demands/{demand}", "Edit"),
    ("Clients / Add New Client", "/clients", "Add Client"),
    ("ClientDetail / Edit Client", "/clients/{client}", "Edit"),
]

# Runs in the page. Returns the geometry of the topmost dialog, and for every
# button: whether it is visible immediately, and whether scrollIntoView() can
# bring it on screen (which is what separates CLIPPED from BELOWFOLD).
PROBE_JS = """() => {
  const dialogs = [...document.querySelectorAll('[role=dialog]')]
    .filter(e => e.getBoundingClientRect().height > 40);
  if (!dialogs.length) return {error: 'no [role=dialog] on screen'};
  const card = dialogs[dialogs.length - 1];
  const cr = card.getBoundingClientRect();
  const buttons = [];
  for (const b of card.querySelectorAll('button, input[type=submit]')) {
    let r = b.getBoundingClientRect();
    if (r.height === 0 || r.width === 0) continue;       // hidden control
    const label = (b.textContent || b.value || b.getAttribute('aria-label') || '')
      .trim().slice(0, 24) || '(icon)';
    const immediate = r.top >= -1 && r.bottom <= innerHeight + 1;
    if (!immediate) { b.scrollIntoView({block: 'center'}); r = b.getBoundingClientRect(); }
    const afterScroll = r.top >= -1 && r.bottom <= innerHeight + 1;
    buttons.push({label, immediate, afterScroll});
  }
  return {
    viewport: {w: innerWidth, h: innerHeight},
    card: {top: Math.round(cr.top), bottom: Math.round(cr.bottom),
           height: Math.round(cr.height), width: Math.round(cr.width)},
    cardScrolls: card.scrollHeight > card.clientHeight + 1,
    // The overlay must fill the viewport exactly; anything else means a
    // containing-block ancestor has captured it (the section 14.3 defect).
    overlayFillsViewport: (() => {
      const ov = card.parentElement;
      if (!ov) return null;
      const r = ov.getBoundingClientRect();
      return Math.round(r.width) === innerWidth && Math.round(r.height) === innerHeight
             && Math.round(r.top) === 0 && Math.round(r.left) === 0;
    })(),
    clipped: buttons.filter(b => !b.afterScroll).map(b => b.label),
    belowFold: buttons.filter(b => !b.immediate && b.afterScroll).map(b => b.label),
    buttonCount: buttons.length,
  };
}"""


def _env_credentials() -> tuple[str, str]:
    email = os.environ.get("ADMIN_EMAIL")
    pw = os.environ.get("ADMIN_PW")
    if email and pw:
        return email, pw
    env_path = os.path.join(REPO_ROOT, ".env")
    found: dict[str, str] = {}
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r"\s*(DEFAULT_ADMIN_EMAIL|DEFAULT_ADMIN_PASSWORD)\s*=\s*(.*)", line)
                if m:
                    found[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return (email or found.get("DEFAULT_ADMIN_EMAIL", ""),
            pw or found.get("DEFAULT_ADMIN_PASSWORD", ""))


def _sample_ids() -> dict[str, str]:
    """Real row ids, so detail-page dialogs can be reached."""
    import sqlite3
    out = {"demand": "", "client": ""}
    db = os.path.join(REPO_ROOT, "data", "dogar_trading.db")
    if not os.path.exists(db):
        return out
    try:
        con = sqlite3.connect(db)
        for key, table in (("demand", "demands"), ("client", "clients")):
            row = con.execute(f"SELECT id FROM {table} LIMIT 1").fetchone()  # noqa: S608
            if row:
                out[key] = str(row[0])
        con.close()
    except sqlite3.Error:
        pass
    return out


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

    vw, vh = 1024, 640
    if os.environ.get("VIEWPORT"):
        m = re.match(r"(\d+)x(\d+)$", os.environ["VIEWPORT"])
        if m:
            vw, vh = int(m.group(1)), int(m.group(2))

    ids = _sample_ids()
    print(f"base={base}  viewport={vw}x{vh}  admin={email}")

    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": vw, "height": vh},
                                  ignore_https_errors=True)
        # NOTE: the login body key is `username`, and it must be sent as JSON.
        resp = ctx.request.post(f"{base}/api/auth/login",
                                data={"username": email, "password": pw})
        if not resp.ok:
            print(f"login failed: HTTP {resp.status} {resp.text()[:200]}")
            print("if this is a 401 over plain http://, the secure access_token "
                  "cookie was dropped -- pass the HTTPS URL")
            ctx.close(); browser.close()
            return 4
        page = ctx.new_page()
        for label, route, needle in CASES:
            row: dict[str, object] = {"label": label, "route": route}
            try:
                # `domcontentloaded`, not `networkidle`: several pages poll for
                # live activity, so networkidle never settles and times out.
                page.goto(f"{base}{route.format(**ids)}", wait_until="domcontentloaded")
                page.wait_for_timeout(2500)
                # Wait for the trigger rather than probing once and giving up.
                # Detail routes (/demands/{id}, /clients/{id}) fetch the record
                # after mount, so their action bar can appear later than a list
                # page's. A single check at a fixed 2500ms made
                # "DemandDetail / Edit" flap between `ok` and `SKIP` purely on
                # timing -- and a SKIP is indistinguishable from a pass in the
                # exit code, so a genuinely deleted button would have been
                # reported as success. Verified by probing the page directly:
                # the Edit button is present with no JS errors, so the flap was
                # the harness's, not the app's.
                btn = page.locator("button", has_text=needle).first
                try:
                    btn.wait_for(state="visible", timeout=8000)
                except Exception:  # noqa: BLE001
                    pass
                if btn.count() == 0:
                    row["missing"] = (
                        f"no button matching {needle!r} after 10.5s -- either the "
                        f"control was removed/renamed, or it is permission-gated "
                        f"for this account"
                    )
                    results.append(row); continue
                btn.click()
                page.wait_for_timeout(1100)
                row.update(page.evaluate(PROBE_JS))
            except Exception as exc:  # noqa: BLE001
                row["error"] = f"{type(exc).__name__}: {exc}".replace("\n", " ")[:160]
            results.append(row)
        ctx.close(); browser.close()

    if os.environ.get("DIALOG_GEOMETRY_JSON"):
        with open(os.environ["DIALOG_GEOMETRY_JSON"], "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=1)

    print(f"\n=== DIALOG GEOMETRY @ {vw}x{vh} ===")
    clipped, belowfold, missing, opened = [], [], [], 0
    for r in results:
        label = str(r["label"])
        if r.get("missing"):
            # NOT a skip. A missing trigger means the case was never actually
            # verified, so it must be visible in the exit code -- otherwise the
            # harness reports PASS for a dialog it never opened.
            missing.append(label)
            print(f"  MISSING    {label:34s} {r['missing']}")
        elif r.get("skipped"):
            print(f"  SKIP       {label:34s} {r['skipped']}")
        elif r.get("error"):
            print(f"  ERROR      {label:34s} {r['error']}")
        elif r.get("clipped"):
            opened += 1; clipped.append(label)
            print(f"  CLIPPED    {label:34s} unreachable: {r['clipped']}")
        elif r.get("overlayFillsViewport") is False:
            opened += 1; clipped.append(label)
            print(f"  CLIPPED    {label:34s} overlay does not fill the viewport "
                  f"(containing-block ancestor -- see section 14.3)")
        elif r.get("belowFold"):
            opened += 1; belowfold.append(label)
            print(f"  BELOWFOLD  {label:34s} scroll needed for: {r['belowFold']}")
        else:
            opened += 1
            card = r.get("card", {})
            print(f"  ok         {label:34s} card {card.get('top')}..{card.get('bottom')} "
                  f"of {vh}, {r.get('buttonCount')} controls all on screen")

    print(f"\nopened={opened}  clipped={len(clipped)}  "
          f"belowFold={len(belowfold)}  missing={len(missing)}")
    if missing:
        print("RESULT: FAIL - these dialogs were never opened, so their geometry "
              "is UNVERIFIED: " + ", ".join(missing) +
              "\n  Do not read this as a pass. Either the trigger was removed/"
              "renamed (fix the app or this CASES list), or it is "
              "permission-gated for the account used.")
        return 5
    if opened == 0:
        print("RESULT: FAIL - no dialog could be opened; the harness is broken, "
              "not necessarily the app")
        return 4
    if clipped:
        print("RESULT: FAIL - these dialogs are UNUSABLE: " + ", ".join(clipped))
        return 2
    if belowfold:
        print("RESULT: WARN - usable, but these need scrolling to reach their "
              "actions: " + ", ".join(belowfold) +
              "\n  Pin the action bar with the `spa-modal-foot` class.")
        return 3
    print("RESULT: PASS - every dialog fills the viewport and every control is "
          "reachable without scrolling")
    return 0


if __name__ == "__main__":
    sys.exit(main())
