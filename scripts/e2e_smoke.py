"""Live E2E smoke test for the three reported runtime bugs (2026-07-10).

Bug #1  login page must NOT reload-loop (must be able to type credentials)
Bug #2  in-app navigation must be client-side (sidebar node stays mounted,
        no full document reload)
Bug #3  opening a candidate from a demand file must open the drawer IN PLACE
        (URL must not detour through /candidates)

Run:  python scripts/e2e_smoke.py <base_url> <username> <password>
Exit code 0 = all pass.
"""
import sys
import time

from playwright.sync_api import sync_playwright

BASE = sys.argv[1].rstrip("/")
USER = sys.argv[2]
PW = sys.argv[3]
# Optional 4th arg: a demand id known to have an assigned candidate (makes the
# Bug#3 check conclusive rather than relying on whichever demand is first).
DEMAND_ID = sys.argv[4] if len(sys.argv) > 4 else None

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(ignore_https_errors=True)
    page = ctx.new_page()

    # ── Bug #1: login page does not reload-loop ──────────────────────────────
    nav_count = {"n": 0}
    page.on("framenavigated", lambda f: nav_count.__setitem__("n", nav_count["n"] + 1) if f == page.main_frame else None)
    page.goto(f"{BASE}/login", wait_until="domcontentloaded")
    page.wait_for_selector('input[type="password"]', timeout=15000)
    base_nav = nav_count["n"]
    time.sleep(6)  # if it were looping, main-frame navigations would keep climbing
    extra_nav = nav_count["n"] - base_nav
    # Type into the field to prove it is interactable (a reload would clear/blur it)
    page.fill('input[type="password"]', "typing-probe")
    val = page.input_value('input[type="password"]')
    check("Bug#1 login no reload-loop", extra_nav == 0, f"{extra_nav} extra main-frame navigations in 6s")
    check("Bug#1 password field interactable", val == "typing-probe", f"value={val!r}")

    # ── Log in ───────────────────────────────────────────────────────────────
    page.fill('input[autocomplete="username"]', USER)
    page.fill('input[type="password"]', PW)
    page.click('button[type="submit"]')
    page.wait_for_url("**/dashboard", timeout=20000)
    check("Login succeeds", "/dashboard" in page.url, page.url)

    # ── Bug #2: client-side nav keeps the sidebar node mounted ───────────────
    # Tag the sidebar DOM node; if a full reload happens, the tag is lost.
    page.wait_for_selector("nav, aside", timeout=10000)
    page.evaluate("""() => {
        const el = document.querySelector('aside') || document.querySelector('nav');
        if (el) el.setAttribute('data-e2e-tag', 'sticky');
    }""")
    reloaded = {"v": False}
    page.on("load", lambda _: reloaded.__setitem__("v", True))
    # Navigate via the in-app Demands link (client-side <NavLink>)
    page.click('a[href$="/demands"]')
    page.wait_for_url("**/demands", timeout=15000)
    still_tagged = page.evaluate("""() => {
        const el = document.querySelector('[data-e2e-tag="sticky"]');
        return !!el;
    }""")
    check("Bug#2 SPA nav (no full reload)", still_tagged and not reloaded["v"],
          f"sidebar_tag_preserved={still_tagged} load_event={reloaded['v']}")

    # ── Bug #3: candidate details from a demand file open drawer in place ─────
    # Open the first demand file, go to Trades, click a candidate 'Details'.
    opened_demand = False
    try:
        if DEMAND_ID:
            page.goto(f"{BASE}/demands/{DEMAND_ID}", wait_until="domcontentloaded")
            page.wait_for_url("**/demands/**", timeout=15000)
            opened_demand = True
        else:
            page.goto(f"{BASE}/demands", wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            link = page.query_selector('a[href*="/demands/"]')
            if link:
                link.click()
                page.wait_for_url("**/demands/**", timeout=15000)
                opened_demand = True
    except Exception as e:  # noqa: BLE001
        check("Bug#3 open a demand file", False, str(e))

    if opened_demand:
        detail_url_before = page.url.split("?")[0]
        # Let the demand-detail page finish rendering its tab bar before we look
        # for the Trades tab (the tab buttons are lazy-rendered).
        page.wait_for_timeout(2000)
        # Switch to the Trades tab (its label starts with "Trades") so the
        # candidate cards render.
        trades_clicked = False
        try:
            for t in page.query_selector_all("button"):
                if (t.inner_text() or "").strip().startswith("Trades"):
                    t.click()
                    trades_clicked = True
                    break
        except Exception:
            pass
        # Give the candidate cards time to render after the tab switch.
        page.wait_for_timeout(2000)
        # The candidate card's "Details" button carries a fa-eye icon — that is
        # what opens the drawer. (A plain text match would also hit the "Details"
        # TAB button, so we target the eye-icon button specifically.)
        det = page.query_selector('button:has(i.fa-eye), button:has(i.fa-regular.fa-eye)')
        if det:
            reloaded["v"] = False
            det.scroll_into_view_if_needed()
            det.click()
            page.wait_for_timeout(1500)
            # A dialog (the shared CandidateDrawer) must appear, URL must stay on
            # the demand (must NOT have navigated to /candidates).
            has_dialog = page.query_selector('[role="dialog"]') is not None
            stayed = "/candidates" not in page.url and page.url.startswith(detail_url_before)
            check("Bug#3 candidate drawer opens in place",
                  has_dialog and stayed and not reloaded["v"],
                  f"dialog={has_dialog} url_stayed={stayed} url={page.url} trades={trades_clicked}")
        else:
            check("Bug#3 candidate Details button present", False,
                  "no candidate assigned to this demand to click (inconclusive)")

    browser.close()

print("\n=== SUMMARY ===")
passed = sum(1 for _, ok, _ in results if ok)
for name, ok, _ in results:
    print(f"  {'✓' if ok else '✗'} {name}")
print(f"{passed}/{len(results)} checks passed")
# Only count conclusive checks for exit status.
failed = [n for n, ok, d in results if not ok and "inconclusive" not in d]
sys.exit(1 if failed else 0)
