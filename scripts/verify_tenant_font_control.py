#!/usr/bin/env python3
"""Prove the tenant typeface can be CHANGED BY AN OPERATOR, in a real browser.

WHY A SEPARATE HARNESS
----------------------
The font chain was proven end to end -- column, migration, boot payload,
validated API field, CSS custom property, document stylesheet -- and every
link had tests. What none of them asked was whether an operator could
actually set the value. They could not: no UI control existed, so the only
way to give an office its own typeface was to issue a PUT by hand. A setting
that exists everywhere except where the operator can reach it is not a
delivered feature, and a suite that tests every layer except the one the
human touches will report it as done.

So this harness drives the control the way a person does: open the Portals
admin screen, open a portal's branding drawer, pick a different typeface,
press Save, reload, and check the value came back. It asserts on the SELECT's
own state and on the reloaded value, not on a network payload, because the
defect being guarded against is precisely "the wiring is right but nothing
renders".

Run:
    PORTAL_URL=http://127.0.0.1:3000 ./venv/bin/python \
        scripts/verify_tenant_font_control.py
"""
from __future__ import annotations

import os
import sys

from playwright.sync_api import sync_playwright

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 127.0.0.1, never localhost: localhost resolves ::1 first and uvicorn binds
# IPv4 only, so "localhost" fails with ECONNREFUSED ::1:3000.
BASE = os.environ.get("PORTAL_URL", "http://127.0.0.1:3000")

PROBLEMS: list[str] = []


def fail(msg: str) -> None:
    print(f"  FAIL  {msg}")
    PROBLEMS.append(msg)


def note(msg: str) -> None:
    print(f"  note  {msg}")


def root_creds() -> tuple[str, str]:
    email = os.environ.get("ADMIN_EMAIL")
    pw = os.environ.get("ADMIN_PW")
    if email and pw:
        return email, pw
    env: dict[str, str] = {}
    path = os.path.join(REPO, ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return (env.get("DEFAULT_ADMIN_EMAIL", ""),
            env.get("DEFAULT_ADMIN_PASSWORD", ""))


def main() -> int:
    email, pw = root_creds()
    if not email or not pw:
        print("FAIL: no admin credentials (set ADMIN_EMAIL / ADMIN_PW)")
        return 4

    print("=== TENANT FONT CONTROL ===")
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        # One context at a time: the sandbox has a hard ~985MB ceiling and a
        # second Chromium context has already taken the app server down with it.
        ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
        try:
            r = ctx.request.post(f"{BASE}/api/auth/login",
                                 data={"username": email, "password": pw})
            if r.status != 200:
                fail(f"root login returned {r.status}")
                return 3

            page = ctx.new_page()
            errors: list[str] = []
            page.on("console", lambda m: errors.append(m.text)
                    if m.type == "error" else None)

            page.goto(f"{BASE}/portal-admin", wait_until="domcontentloaded")
            # Wait for measurable content, not mere attachment: the shell is
            # static JSX and would satisfy an "attached" wait on frame one,
            # before the portal rows have arrived.
            try:
                page.wait_for_function(
                    "() => !!document.querySelector"
                    "(\"button[title*='Customize branding']\")",
                    timeout=20000)
            except Exception:  # noqa: BLE001
                fail("no branding/customise control appeared on /portal-admin -- "
                     "cannot reach the typeface setting at all")
                return 3

            # Open the drawer for the first portal offering one.
            # Scoped by title, not by text: the page also renders a
            # "Customize" button for ROLES, and clicking that opens a
            # permission matrix with no typeface control -- the harness would
            # then report the feature missing when it is simply looking at the
            # wrong dialog.
            opener = page.locator("button[title*='Customize branding']").first
            opener.click()

            try:
                page.wait_for_selector("#tenant-font-key", state="visible",
                                       timeout=15000)
            except Exception:  # noqa: BLE001
                fail("the branding drawer opened but has NO typeface control "
                     "(#tenant-font-key) -- the font is unreachable for an "
                     "operator, which is the defect this harness exists for")
                return 3

            sel = page.locator("#tenant-font-key")
            before = sel.input_value()
            options = sel.locator("option").evaluate_all(
                "els => els.map(e => e.value)")
            note(f"control present; current={before!r} options={options}")

            if len(options) < 2:
                fail(f"only {len(options)} typeface option(s) offered; the "
                     f"control cannot express a change")
                return 3

            target = next((o for o in options if o != before), None)
            if target is None:
                fail("every option has the same value")
                return 3

            # Change it, save it, and prove it SURVIVES a reload. Asserting on
            # the in-memory select would pass even if the save never landed.
            sel.select_option(target)
            if sel.input_value() != target:
                fail(f"select did not accept {target!r}")
                return 3

            page.locator("button:has-text('Save Branding')").first.click()
            page.wait_for_timeout(2500)

            page.goto(f"{BASE}/portal-admin", wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            page.locator(
                "button[title*='Customize branding']").first.click()
            page.wait_for_selector("#tenant-font-key", state="visible",
                                   timeout=15000)
            after = page.locator("#tenant-font-key").input_value()

            if after != target:
                fail(f"typeface did not persist: selected {target!r}, after "
                     f"reload the control shows {after!r}")
            else:
                note(f"persisted across reload: {before!r} -> {after!r}")

            # Restore, so the harness leaves no trace in the operator's data.
            page.locator("#tenant-font-key").select_option(before)
            page.locator("button:has-text('Save Branding')").first.click()
            page.wait_for_timeout(2000)
            page.goto(f"{BASE}/portal-admin", wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            page.locator(
                "button[title*='Customize branding']").first.click()
            page.wait_for_selector("#tenant-font-key", state="visible",
                                   timeout=15000)
            restored = page.locator("#tenant-font-key").input_value()
            if restored != before:
                fail(f"could not restore the original typeface: wanted "
                     f"{before!r}, control shows {restored!r} -- this harness "
                     f"has LEFT THE OFFICE'S BRANDING CHANGED")
            else:
                note(f"restored to {restored!r}; no residue left behind")

            # A screenshot is evidence, never a verdict: on this sandbox
            # Page.screenshot intermittently exceeds its timeout under memory
            # pressure, and "could not take a picture" must not look like
            # "the feature is broken".
            try:
                page.screenshot(path="/tmp/font_control.png", timeout=15000)
            except Exception as exc:  # noqa: BLE001
                note(f"screenshot not captured ({type(exc).__name__}); "
                     f"assertions above were still measured")

            real_errors = [e for e in errors if "401" not in e]
            if real_errors:
                fail(f"console errors: {real_errors[:3]}")
        finally:
            ctx.close()
            browser.close()

    if PROBLEMS:
        print(f"\nRESULT: FAIL -- {len(PROBLEMS)} problem(s)")
        return 1
    print("\nRESULT: PASS -- an operator can select a typeface, it persists, "
          "and the harness restored the original")
    return 0


if __name__ == "__main__":
    sys.exit(main())
