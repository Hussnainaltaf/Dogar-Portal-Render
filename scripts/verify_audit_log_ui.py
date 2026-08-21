#!/usr/bin/env python3
"""Verify the Audit Log filters/search IN A REAL BROWSER.

WHY THIS SCRIPT EXISTS
----------------------
The operator asked to "find denied or successful actions by office, user,
document type, and date range". The API side of that is covered by
tests/test_audit_denials.py and tests/test_audit_doc_category.py (30 tests,
mutation-proven). Neither can answer the question the operator actually cares
about, which is not "does the endpoint filter?" but "does the PAGE filter?"

Three specific ways this feature can be broken while every backend test stays
green, all of which have real precedent in this project:

  1. The control renders but is never wired to the request, so choosing
     "Denied" changes nothing. A static test of the endpoint cannot see this.
  2. The request is sent but the response is dropped or raced -- the
     out-of-order-response defect already fixed once on the Roles page. A
     slow metadata call landing after a fast filter call silently restores
     the unfiltered list.
  3. The page throws during render (a lazy chunk that 404s, a null in the
     metadata shape) and the SPA shows an empty shell. HTTP 200 on the API,
     nothing usable on screen.

WHAT IT ASSERTS
  1. the page renders its real heading and its filter controls exist
  2. the metadata-driven Outcome dropdown actually offers "denied"
  3. choosing Denied CHANGES the rendered row count (and the rows shown are
     denials) -- compared against the unfiltered count, because a filter that
     is accepted-but-ignored renders everything, and everything contains the
     row you were looking for
  4. typing in the search box narrows the list, and clearing restores it
  5. a filter combination that cannot match renders a distinct empty state,
     not a silent full list
  6. the date-range inputs exist and are of type=date
  7. no console errors on any of the above

It writes screenshots to /tmp/audit_<label>.png so the result is visually
reviewable rather than merely asserted.

USAGE
    PORTAL_URL=http://localhost:3000 ./venv/bin/python scripts/verify_audit_log_ui.py

NOTE ON THE URL: the local PM2 server serves plain HTTP, and the access_token
cookie is issued secure=True only when the request arrives over TLS, so
http://localhost works locally. Against a deployed HTTPS host, pass that URL.

Credentials come from $ADMIN_EMAIL / $ADMIN_PW, falling back to
DEFAULT_ADMIN_EMAIL / DEFAULT_ADMIN_PASSWORD in .env. Never from source.

EXIT STATUS
    0 = every assertion held
    2 = a real defect was measured
    4 = harness problem (no playwright, login failed, no seed data)
"""
from __future__ import annotations

import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ROUTE = "/admin/audit-log"

# Element ids the page assigns. Asserting on ids rather than on text means a
# copy change cannot fail this harness, but a control that was never rendered
# still does.
CONTROLS = {
    "outcome": "#audit-f-outcome",
    "user": "#audit-f-user",
    "doctype": "#audit-f-doctype",
    "entity": "#audit-f-entity",
    "action": "#audit-f-action",
    "from": "#audit-f-from",
    "to": "#audit-f-to",
    "search": "#audit-search",
    "clear": "#audit-clear-filters",
}


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


def main() -> int:  # noqa: PLR0915 -- a linear browser script; splitting it
    #                              would hide the order the assertions depend on
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed: ./venv/bin/pip install playwright "
              "&& ./venv/bin/python -m playwright install chromium")
        return 4

    base = (sys.argv[1] if len(sys.argv) > 1
            else os.environ.get("PORTAL_URL", "http://localhost:3000")).rstrip("/")
    email, pw = _env_credentials()
    if not email or not pw:
        print("no admin credentials: set $ADMIN_EMAIL and $ADMIN_PW, or provide "
              "DEFAULT_ADMIN_EMAIL / DEFAULT_ADMIN_PASSWORD in .env")
        return 4

    print(f"base={base}  route={ROUTE}  admin={email}")

    problems: list[str] = []
    notes: list[str] = []
    console_errors: list[str] = []

    def rows(page) -> int:
        """Count REAL data rows only, via the data-audit-row marker.

        Counting `#audit-results tbody tr` would be wrong: the loading spinner
        and the "No entries match these filters" message are each rendered as a
        single <tr>, so an empty result would score 1 and a correctly-filtered
        page would look like a filter that did nothing. The marker is only on
        rows built from a response item.
        """
        return page.evaluate(
            "() => document.querySelectorAll('#audit-results [data-audit-row]').length"
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 900},
                                  ignore_https_errors=True)
        # The login body key is `username`, sent as JSON.
        resp = ctx.request.post(f"{base}/api/auth/login",
                                data={"username": email, "password": pw})
        if not resp.ok:
            print(f"login failed: HTTP {resp.status} {resp.text()[:200]}")
            ctx.close(); browser.close()
            return 4

        # SEED CHECK. Every assertion below compares counts, so a trail with no
        # denials in it cannot distinguish "the filter works" from "the filter
        # returns nothing". Refusing to run is honest; passing on an empty
        # dataset would be a false green.
        meta = ctx.request.get(f"{base}/api/users/audit-log/_meta/filters")
        if not meta.ok:
            print(f"filter metadata unavailable: HTTP {meta.status}")
            ctx.close(); browser.close()
            return 4
        counts = (meta.json() or {}).get("outcome_counts") or {}
        if not counts.get("denied"):
            print("NO DENIED ROWS in the trail -- this harness cannot prove a "
                  "denial filter against data that contains no denials. "
                  "Trigger one (log in as a non-admin and hit a guarded route) "
                  "and re-run.")
            ctx.close(); browser.close()
            return 4
        print(f"seed: denied={counts.get('denied')} success={counts.get('success')}")

        page = ctx.new_page()
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)

        # ---- 1. the page renders at all ---------------------------------
        page.goto(f"{base}{ROUTE}", wait_until="domcontentloaded")
        try:
            page.wait_for_selector("#audit-log-heading", state="attached", timeout=15000)
        except Exception:  # noqa: BLE001
            problems.append(
                "the Audit Log page never rendered its heading (#audit-log-heading). "
                "The route, the lazy chunk or the page's first render is broken -- "
                "note the API can be perfectly healthy in this state.")
            page.screenshot(path="/tmp/audit_norender.png", full_page=False)
            ctx.close(); browser.close()
            for pr in problems:
                print(f"FAIL {pr}")
            return 2

        # Wait for the first result set rather than a fixed delay.
        try:
            page.wait_for_selector("#audit-results", state="attached", timeout=15000)
        except Exception:  # noqa: BLE001
            notes.append("#audit-results never attached")
        page.wait_for_timeout(1500)
        page.screenshot(path="/tmp/audit_initial.png", full_page=False)

        for name, sel in CONTROLS.items():
            if page.query_selector(sel) is None:
                problems.append(
                    f"filter control '{name}' ({sel}) is not on the page -- the "
                    "operator asked for this dimension by name")

        # date inputs must be real date pickers, not free text
        for name in ("from", "to"):
            el = page.query_selector(CONTROLS[name])
            if el is not None:
                t = (el.get_attribute("type") or "").lower()
                if t != "date":
                    problems.append(
                        f"the '{name}' date control is type={t!r}, not 'date' -- "
                        "a free-text date is how unparseable ranges get sent")

        baseline = rows(page)
        print(f"unfiltered rendered rows: {baseline}")
        if baseline == 0:
            problems.append(
                "the unfiltered page rendered ZERO rows while the API reports "
                f"{counts.get('success', 0) + counts.get('denied', 0)} entries -- "
                "the list is not being rendered from the response")

        # ---- 2. the Outcome dropdown really offers 'denied' -------------
        opts = page.evaluate(
            "() => Array.from(document.querySelectorAll('#audit-f-outcome option'))"
            ".map(o => o.value)")
        if "denied" not in opts:
            problems.append(
                f"the Outcome dropdown offers {opts!r} -- 'denied' is absent, so "
                "the operator cannot choose the case they asked for. This is what "
                "an empty/derived metadata list looks like on screen.")

        # ---- 3. choosing Denied narrows the list -----------------------
        if "denied" in opts:
            page.select_option(CONTROLS["outcome"], "denied")
            page.wait_for_timeout(1800)
            page.screenshot(path="/tmp/audit_denied.png", full_page=False)
            denied_rows = rows(page)
            print(f"outcome=denied rendered rows: {denied_rows}")
            if denied_rows == 0:
                problems.append(
                    "selecting Denied rendered zero rows even though the API "
                    f"reports {counts['denied']} denial(s) -- the control is wired "
                    "to nothing, or the response is being dropped")
            elif baseline and denied_rows >= baseline:
                problems.append(
                    f"selecting Denied rendered {denied_rows} rows vs {baseline} "
                    "unfiltered -- the filter was accepted and ignored. Note this "
                    "state LOOKS fine: the denial you wanted is on screen, "
                    "surrounded by everything else.")
            else:
                # The rows shown must actually BE denials. Two independent
                # readings are taken: the row's own outcome attribute (what the
                # page believes it received) and the visible badge text (what
                # the operator actually sees). A page that filters correctly but
                # paints every row "Success" is still broken.
                seen = page.evaluate(
                    "() => Array.from(document.querySelectorAll("
                    "'#audit-results [data-audit-row]')).map(tr => ({"
                    "attr: tr.getAttribute('data-audit-outcome'),"
                    "text: tr.innerText.toLowerCase()}))")
                wrong_attr = [s for s in seen if s["attr"] != "denied"]
                if wrong_attr:
                    problems.append(
                        f"{len(wrong_attr)} of {len(seen)} rows under the Denied "
                        "filter are not denials -- the page rendered rows the "
                        "filter should have excluded")
                wrong_text = [s for s in seen if "denied" not in s["text"]]
                if wrong_text:
                    problems.append(
                        f"{len(wrong_text)} of {len(seen)} rows under the Denied "
                        "filter do not READ as denials on screen -- the badge "
                        "disagrees with the data behind it")

            # ---- 5. a contradictory combination shows an empty state ----
            # A document view is a SUCCESS. Asking for DENIED rows of a document
            # type that only ever succeeded must render an explicit empty state.
            dt_opts = page.evaluate(
                "() => Array.from(document.querySelectorAll('#audit-f-doctype option'))"
                ".map(o => o.value).filter(v => v && v !== 'all')")
            if dt_opts:
                page.select_option(CONTROLS["doctype"], dt_opts[0])
                page.wait_for_timeout(1800)
                page.screenshot(path="/tmp/audit_empty.png", full_page=False)
                combo_rows = rows(page)
                body = page.inner_text("#audit-results") if page.query_selector(
                    "#audit-results") else ""
                if combo_rows != 0:
                    problems.append(
                        f"denied + document type {dt_opts[0]!r} rendered "
                        f"{combo_rows} rows; those two filters are not being "
                        "combined (the endpoint ANDs them, so the page is "
                        "sending only one of them)")
                elif not body.strip():
                    problems.append(
                        "an impossible filter combination rendered a completely "
                        "blank area with no empty-state message -- "
                        "indistinguishable from a page that failed to load")
                else:
                    notes.append(f"empty state reads: {body.strip()[:90]!r}")
                page.select_option(CONTROLS["doctype"], "all")
                page.wait_for_timeout(1200)
            else:
                notes.append("no document types in the dropdown; skipped the "
                             "combination check")

            # reset outcome for the search test
            page.select_option(CONTROLS["outcome"], "all")
            page.wait_for_timeout(1500)

        # ---- 4. search narrows, and clearing restores ------------------
        restored = rows(page)
        if baseline and restored < baseline:
            problems.append(
                f"after resetting the filters the list shows {restored} rows but "
                f"started at {baseline} -- resetting does not restore, so an "
                "operator who filters once sees a permanently truncated trail")

        page.fill(CONTROLS["search"], "zzz-no-such-text-zzz")
        # The page debounces the typed query by 300ms; 1800ms covers debounce +
        # request + render without depending on machine speed.
        page.wait_for_timeout(1800)
        page.screenshot(path="/tmp/audit_search_miss.png", full_page=False)
        miss = rows(page)
        print(f"search miss rendered rows: {miss}")
        if miss != 0:
            problems.append(
                f"searching for text that cannot exist rendered {miss} rows -- the "
                "search box is not reaching the request")

        page.fill(CONTROLS["search"], "")
        page.wait_for_timeout(1800)
        page.screenshot(path="/tmp/audit_search_cleared.png", full_page=False)
        back = rows(page)
        print(f"search cleared rendered rows: {back}")
        if baseline and back < baseline:
            problems.append(
                f"clearing the search left {back} rows (started {baseline}) -- the "
                "list does not recover, which reads as data loss")

        ctx.close()
        browser.close()

    # Console errors are a defect: this page loads a lazy chunk and a
    # metadata call, both of which fail silently on screen.
    real_errors = [e for e in console_errors
                   if "favicon" not in e.lower() and "404" not in e]
    if real_errors:
        problems.append(f"{len(real_errors)} console error(s): {real_errors[:3]}")

    for n in notes:
        print(f"note {n}")
    if problems:
        print()
        for pr in problems:
            print(f"FAIL {pr}")
        print(f"\nRESULT: {len(problems)} defect(s) measured")
        return 2
    print("\nRESULT: PASS -- audit log filters and search verified in a real browser")
    print("screenshots: /tmp/audit_initial.png /tmp/audit_denied.png "
          "/tmp/audit_empty.png /tmp/audit_search_miss.png "
          "/tmp/audit_search_cleared.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
