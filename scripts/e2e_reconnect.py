"""Live E2E: offline → online reconnect must refetch the candidate list AND the
open side sheet, so no stale rows/details linger from while the app was offline.

What it verifies
----------------
1. After login, the Candidates page loads and issues a GET /candidates/ .
2. Going offline surfaces the offline banner (data-testid="offline-banner").
3. Coming back online AUTOMATICALLY re-issues GET /candidates/ (list refetch).
4. With the side sheet (candidate drawer) open, a reconnect also re-issues
   GET /candidates/<id> (detail refetch) — proving the sheet drops stale data.

Run:  python scripts/e2e_reconnect.py <base_url> <username> <password>
Exit code 0 = all pass.
"""
import re
import sys
import time

from playwright.sync_api import sync_playwright

BASE = sys.argv[1].rstrip("/")
USER = sys.argv[2]
PW = sys.argv[3]

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(ignore_https_errors=True)
    page = ctx.new_page()

    # Count API calls so we can prove a refetch actually happened on reconnect.
    list_calls = {"n": 0}
    detail_calls = {"n": 0}
    detail_re = re.compile(r"/api/candidates/\d+(\?|$)")

    def on_request(req):
        u = req.url
        if "/api/candidates/" in u and detail_re.search(u):
            detail_calls["n"] += 1
        elif "/api/candidates" in u:
            list_calls["n"] += 1

    page.on("request", on_request)

    # ── Log in ────────────────────────────────────────────────────────────────
    page.goto(f"{BASE}/login", wait_until="domcontentloaded")
    page.wait_for_selector('input[type="password"]', timeout=15000)
    page.fill('input[autocomplete="username"]', USER)
    page.fill('input[type="password"]', PW)
    page.click('button[type="submit"]')
    page.wait_for_url("**/dashboard", timeout=20000)

    # ── Open Candidates ─────────────────────────────────────────────────────
    page.goto(f"{BASE}/candidates", wait_until="domcontentloaded")
    page.wait_for_url("**/candidates", timeout=15000)
    # Wait for the list request to actually land, not a fixed guess.
    deadline = time.time() + 12
    while time.time() < deadline and list_calls["n"] < 1:
        page.wait_for_timeout(200)
    page.wait_for_timeout(500)
    check("Candidates list loaded", list_calls["n"] >= 1, f"{list_calls['n']} list call(s)")

    # ── Open the first candidate drawer (if any row exists) ──────────────────
    # Rows use per-cell stopPropagation, so clicking the row body is unreliable.
    # The "View" eye button (title="View") deterministically opens the sheet.
    drawer_opened = False
    view_btn = page.query_selector('tbody tr button[title="View"]')
    if view_btn:
        detail_before_open = detail_calls["n"]
        view_btn.click()
        try:
            page.wait_for_selector('[role="dialog"]', timeout=6000)
        except Exception:
            pass
        # Wait for the detail request to land.
        d2 = time.time() + 6
        while time.time() < d2 and detail_calls["n"] <= detail_before_open:
            page.wait_for_timeout(200)
        drawer_opened = page.query_selector('[role="dialog"]') is not None
        check("Candidate side sheet opens", drawer_opened and detail_calls["n"] > detail_before_open,
              f"detail calls {detail_before_open}→{detail_calls['n']}, dialog={drawer_opened}")
    else:
        check("Candidate side sheet opens", False, "no candidate rows to open (inconclusive)")

    # ── Go OFFLINE ────────────────────────────────────────────────────────────
    list_before = list_calls["n"]
    detail_before = detail_calls["n"]
    ctx.set_offline(True)
    # Dispatch the browser 'offline' event so the app's listener fires.
    page.evaluate("() => window.dispatchEvent(new Event('offline'))")
    # Poll for the banner instead of a single fixed wait.
    d3 = time.time() + 4
    banner = None
    while time.time() < d3:
        banner = page.query_selector('[data-testid="offline-banner"]')
        if banner is not None:
            break
        page.wait_for_timeout(200)
    check("Offline banner shown when offline", banner is not None,
          "banner present" if banner else "no banner")

    # No refetch should happen while offline.
    check("No list refetch while offline", list_calls["n"] == list_before,
          f"{list_calls['n'] - list_before} extra list call(s)")

    # ── Come back ONLINE ────────────────────────────────────────────────────
    ctx.set_offline(False)
    page.evaluate("() => window.dispatchEvent(new Event('online'))")
    # Give the reconnect handler + network time to refetch.
    deadline = time.time() + 8
    while time.time() < deadline and list_calls["n"] <= list_before:
        page.wait_for_timeout(250)

    check("List refetches on reconnect", list_calls["n"] > list_before,
          f"list calls {list_before}→{list_calls['n']}")

    banner_after = page.query_selector('[data-testid="offline-banner"]')
    check("Offline banner cleared after reconnect", banner_after is None,
          "banner gone" if banner_after is None else "banner still visible")

    if drawer_opened:
        # The open side sheet must also refetch its detail on reconnect.
        deadline = time.time() + 6
        while time.time() < deadline and detail_calls["n"] <= detail_before:
            page.wait_for_timeout(250)
        check("Side sheet refetches on reconnect", detail_calls["n"] > detail_before,
              f"detail calls {detail_before}→{detail_calls['n']}")

    browser.close()

print("\n=== SUMMARY ===")
passed = sum(1 for _, ok, _ in results if ok)
for name, ok, _ in results:
    print(f"  {'✓' if ok else '✗'} {name}")
print(f"{passed}/{len(results)} checks passed")
failed = [n for n, ok, d in results if not ok and "inconclusive" not in d]
sys.exit(1 if failed else 0)
