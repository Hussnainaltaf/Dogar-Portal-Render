#!/usr/bin/env python3
"""Playwright half of ``scripts/verify_comb_geometry.py`` -- do not run directly.

Reads a JSON job on stdin, logs into the portal in a real Chromium, opens each
template's print view and reports the measured ``getBoundingClientRect()``
centre of every rendered comb character. Writes JSON to stdout.

Kept in a separate process because Playwright's sync API cannot run inside an
event loop and because a browser crash must not take the reporting half down
with it.
"""
from __future__ import annotations

import json
import sys


def main() -> int:
    job = json.loads(sys.stdin.read())
    base = job["base"]
    from playwright.sync_api import sync_playwright

    out = []
    with sync_playwright() as p:
        # LOW-MEMORY LAUNCH.
        #
        # The print page inlines the form's background scan as a base64 data:
        # URI (deliberately -- see documents.py `_inline_bg`), so decoding it
        # costs the renderer several times the JPEG's size. In a memory-
        # constrained container that reliably killed the renderer process, and
        # Playwright reported it only as the very unhelpful "Page.goto: Page
        # crashed" -- which looks like a bug in the page rather than an OOM.
        #
        # --disable-dev-shm-usage moves shared memory to /tmp (the container's
        # /dev/shm is typically 64 MB), and the rest drop subsystems this probe
        # never uses. `single-process` is deliberately NOT used: it makes a
        # renderer OOM take the whole browser down with no diagnostics at all.
        browser = p.chromium.launch(args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-features=site-per-process,TranslateUI",
            "--js-flags=--max-old-space-size=256",
        ])
        ctx = browser.new_context(ignore_https_errors=True,
                                  viewport={"width": 1400, "height": 1000})
        page = ctx.new_page()

        # LOGIN. Two gotchas, both learned the hard way:
        #  * the request body key is `username`, not `email`;
        #  * state-changing calls need x-csrf-token matching the csrf_token
        #    cookie -- not needed for the GETs below, but the login itself must
        #    succeed for the secure access_token cookie to be set at all.
        r = ctx.request.post(f"{base}/api/auth/login", data={
            "username": job["email"], "password": job["password"],
        })
        if not r.ok:
            print(f"login failed: {r.status} {r.text()[:300]}", file=sys.stderr)
            return 1

        for t in job["targets"]:
            url = f"{base}/api/documents/templates/{t['tpl_id']}/print?auto=0"
            if t.get("record_id"):
                url += f"&record_id={t['record_id']}"
            # `networkidle` is wrong for this app: several pages poll for live
            # activity, so "no network for 500ms" may never happen and the wait
            # burns the whole timeout. The comb spans are emitted server-side in
            # the HTML, so they exist as soon as the DOM is parsed -- the
            # background image finishing has no bearing on their geometry.
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector(".ov-field.ov-cells", timeout=30000)

            fields = page.evaluate("""() => {
              const out = []
              for (const box of document.querySelectorAll('.ov-field.ov-cells')) {
                const b = box.getBoundingClientRect()
                const chars = []
                // EVERY span is reported, INCLUDING whitespace-only ones.
                // A `format: "text"` comb (OEP Name / Address) deliberately
                // gives a space its own printed box, and the caller matches
                // spans to cells BY POSITION. Skipping blank spans shifts every
                // character after a space one cell left and produces a false
                // failure of exactly one cell pitch -- which is how this probe
                // first "found" a 29.35pt error in a comb whose real worst
                // offset is 0.01pt.
                for (const s of box.querySelectorAll('span')) {
                  const r = s.getBoundingClientRect()
                  chars.push({
                    t: (s.textContent || ''),
                    cx: (r.left + r.right) / 2 - b.left,
                    w: r.width,
                  })
                }
                out.push({
                  field_key: box.getAttribute('data-field-key'),
                  chars,
                })
              }
              return out
            }""")

            # Pair each rendered box with its spec. Two rows of the same comb
            # share a field_key, so they are matched in document order: the
            # renderer emits fields in the order they are stored, and the
            # continuation row is the one that is NOT first.
            specs = t["fields"]
            seen = {}
            paired = []
            for f in fields:
                key = f["field_key"]
                idx = seen.get(key, 0)
                seen[key] = idx + 1
                matches = [s for s in specs if s["field_key"] == key]
                spec = matches[idx] if idx < len(matches) else None
                paired.append({
                    "field_key": key,
                    "char_offset": (spec or {}).get("char_offset", 0),
                    "chars": f["chars"],
                })
            out.append({
                "tpl_id": t["tpl_id"], "name": t["name"],
                "source": t.get("source"),
                "record_id": t.get("record_id"),
                "fields": paired,
            })

        browser.close()

    sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
