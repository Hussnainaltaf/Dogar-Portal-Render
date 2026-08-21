"""
CSP policy regression tests.

WHY THIS FILE EXISTS
--------------------
The 2026-07-26 external audit flagged `style-src 'unsafe-inline'` as MEDIUM.
The finding was partly stale: the blanket directive had already been split into
CSP3 granular directives. The remaining exception is `style-src-attr
'unsafe-inline'`, which was empirically proven inert on 2026-07-28 with a real
browser (Playwright):

  1. a style ATTRIBUTE cannot execute script -- legacy IE `expression()` is
     dead in modern engines, so `style="width: expression(...)"` runs nothing;
  2. an injected `<style>` ELEMENT is BLOCKED by strict `style-src-elem`;
  3. an injected inline `<script>` is BLOCKED by the `script-src` nonce.

DECISION (2026-07-28): accept `style-src-attr 'unsafe-inline'` for the shell.
REVIEWED (2026-08-04): runtime-computed progress widths and tenant colours were
moved out of HTML `style="..."` string injection and into CSSOM updates. The
remaining exception is still needed for legacy shell/runtime DOM surfaces, but it
must not grow back to cover avoidable dynamic template style injection.

REVISITED (2026-08-04): the exception is now SHELL-ONLY.
The decision above still holds for the Jinja and SPA shells -- they build nodes
at runtime via `safe_dom.js`, which calls `setAttribute('style', ...)`, so they
genuinely require the exception. But the *standalone document renders* do not:
they are produced server-side in one pass with no runtime DOM building, so their
style attributes can simply be removed at render time. `app/core/style_hoist.py`
hoists them into a nonced `<style>` element, and those four routes now serve with
`style-src-attr 'none'` -- see `tests/test_csp_zero_exception.py`.

SCOPE OF THIS FILE
------------------
These tests probe `/login`, i.e. the SHELL, so they assert the shell's policy:
the accepted `style-src-attr 'unsafe-inline'`. That is deliberate. The shell
losing its exception would BREAK the UI, and a hoisted response regaining one
would be a security regression -- two opposite failures, so they are ratcheted
in two different files. Do not "fix" this file by expecting `'none'` here.

What must NOT happen is the *strict* directives quietly weakening. These tests
are the ratchet. They fail if:
  * script-src loses its nonce or gains 'unsafe-inline' / 'unsafe-eval',
  * style-src-elem loses its nonce or gains 'unsafe-inline',
  * the accepted exception silently widens beyond style-src-attr.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
ROOT = Path(__file__).resolve().parents[1]


def _csp() -> dict[str, str]:
    """Fetch a real page and parse its CSP into {directive: value}."""
    r = client.get("/login")
    assert r.status_code == 200, f"/login returned {r.status_code}"
    raw = r.headers.get("content-security-policy")
    assert raw, "no Content-Security-Policy header on /login"
    out: dict[str, str] = {}
    for part in raw.split(";"):
        part = part.strip()
        if not part:
            continue
        name, _, value = part.partition(" ")
        out[name.strip()] = value.strip()
    return out


def test_script_src_is_nonce_based_and_has_no_unsafe_escape():
    d = _csp()
    script = d.get("script-src")
    assert script, "script-src missing entirely"
    assert re.search(r"'nonce-[A-Za-z0-9_\-]+'", script), (
        f"script-src must be nonce-based, got: {script!r}"
    )
    for bad in ("'unsafe-inline'", "'unsafe-eval'"):
        assert bad not in script, (
            f"script-src must never contain {bad} -- that reopens stored XSS "
            f"to full script execution. Got: {script!r}"
        )


def test_style_src_elem_is_strict():
    """<style> blocks and <link> stylesheets must stay nonce-gated.

    This is the directive that actually matters: an attacker who can inject a
    <style> element can exfiltrate data via CSS selectors + background URLs.
    """
    d = _csp()
    elem = d.get("style-src-elem")
    assert elem, (
        "style-src-elem missing -- without it, browsers fall back to the "
        "permissive legacy style-src and injected <style> becomes possible"
    )
    assert re.search(r"'nonce-[A-Za-z0-9_\-]+'", elem), (
        f"style-src-elem must be nonce-based, got: {elem!r}"
    )
    assert "'unsafe-inline'" not in elem, (
        f"style-src-elem must never allow 'unsafe-inline' -- that is the "
        f"CSS-exfiltration path. Got: {elem!r}"
    )


def test_unsafe_inline_is_confined_to_the_accepted_style_attr_exception():
    """'unsafe-inline' may appear ONLY in style-src-attr and legacy style-src.

    style-src (no suffix) is retained solely as the fallback for browsers that
    do not implement the CSP3 granular directives; conforming browsers ignore
    it when style-src-elem / style-src-attr are present.
    """
    d = _csp()
    allowed = {"style-src-attr", "style-src"}
    offenders = {
        name: val
        for name, val in d.items()
        if "'unsafe-inline'" in val and name not in allowed
    }
    assert not offenders, (
        f"'unsafe-inline' leaked into directives outside the accepted "
        f"exception {sorted(allowed)}: {offenders}"
    )


def test_baseline_directives_present():
    d = _csp()
    for directive in ("default-src", "frame-ancestors", "img-src", "connect-src"):
        assert directive in d, f"{directive} missing from CSP"
    assert "'self'" in d["default-src"], "default-src must be 'self'-based"


def test_blank_target_links_require_noopener_noreferrer():
    """New-tab links must not expose window.opener to rendered/untrusted pages."""
    offenders: list[str] = []
    scanned = 0
    found_links = 0

    # Legacy Jinja tree (kept for branches that predate its removal).
    templates = ROOT / "app/templates"
    if templates.is_dir():
        for path in sorted(templates.glob("*.html")):
            src = path.read_text(encoding="utf-8")
            scanned += 1
            for match in re.finditer(r"<a\b[^>]*\btarget=\"_blank\"[^>]*>", src):
                tag = match.group(0)
                found_links += 1
                rel = re.search(r"\brel=\"([^\"]*)\"", tag)
                tokens = set((rel.group(1) if rel else "").split())
                if not {"noopener", "noreferrer"}.issubset(tokens):
                    line = src.count("\n", 0, match.start()) + 1
                    offenders.append(f"{path.relative_to(ROOT)}:{line}: {tag}")

    # The live React SPA. JSX <a> tags can span several lines, so match the
    # whole element opening tag non-greedily rather than line-by-line.
    jsx_anchor = re.compile(r"<a\b[^>]*?target=\{?['\"]_blank['\"]\}?[^>]*?>", re.S)
    for path in sorted((ROOT / "app/frontend/src").rglob("*.tsx")):
        src = path.read_text(encoding="utf-8", errors="replace")
        scanned += 1
        for match in jsx_anchor.finditer(src):
            tag = match.group(0)
            found_links += 1
            rel = re.search(r"\brel=\{?['\"]([^'\"]*)['\"]\}?", tag)
            tokens = set((rel.group(1) if rel else "").split())
            # noreferrer alone also severs window.opener in every browser that
            # matters, but require both so the intent is explicit and a later
            # edit that drops noreferrer cannot silently reopen the hole.
            if not {"noopener", "noreferrer"}.issubset(tokens):
                line = src.count("\n", 0, match.start()) + 1
                offenders.append(
                    f"{path.relative_to(ROOT)}:{line}: {' '.join(tag.split())[:120]}"
                )

    assert scanned, "no files scanned — the tabnabbing scan roots are wrong"
    assert found_links, (
        "no target=\"_blank\" links found anywhere; if that is real, delete this "
        "test — otherwise the matcher has gone blind"
    )
    assert not offenders, (
        "target=\"_blank\" links must include rel=\"noopener noreferrer\" "
        "to prevent tabnabbing/window.opener exposure:\n" + "\n".join(offenders)
    )


def test_runtime_computed_styles_are_not_injected_as_html_attributes():
    """Runtime-computed styles must be CSSOM writes, not style="..." strings.

    Retargeted to the React SPA after the Jinja2 eradication pass removed the
    templates this used to inspect. The distinction still matters under CSP:

      * ``style={{ width: `${pct}%` }}`` in JSX is compiled to CSSOM *property*
        writes (``el.style.width = …``), which CSP does not police — safe, and
        used freely across the SPA.
      * ``el.setAttribute('style', someString)`` writes a style *attribute*,
        which IS policed by ``style-src-attr`` and is the injection surface the
        original test guarded. The long-term goal is
        ``style-src-attr 'none'``, so new attribute writes must not appear.

    The single permitted occurrence is inside the sanitiser itself, which
    scrubs the value with ``scrubStyle()`` before writing it back.
    """
    spa_src = ROOT / "app" / "frontend" / "src"
    sanitizer_rel = "app/frontend/src/lib/sanitizeHtml.ts"

    offenders: list[str] = []
    scanned = 0
    pattern = re.compile(r"""setAttribute\(\s*['"]style['"]""")
    for path in sorted(spa_src.rglob("*.ts*")):
        rel = path.relative_to(ROOT).as_posix()
        scanned += 1
        src = path.read_text(encoding="utf-8", errors="replace")
        for match in pattern.finditer(src):
            if rel == sanitizer_rel:
                continue  # audited: value passes through scrubStyle() first
            line = src.count("\n", 0, match.start()) + 1
            offenders.append(f"{rel}:{line}")

    assert scanned, "no SPA sources scanned — the scan root is wrong"
    assert not offenders, (
        "runtime-computed styles regressed to inline style ATTRIBUTE writes, "
        "which are policed by style-src-attr and block the move to "
        "style-src-attr 'none'. Assign CSSOM properties instead, e.g. "
        "`el.style.width = pct + '%'`:\n" + "\n".join(offenders)
    )

    # The sanitiser must still scrub the one permitted attribute write.
    sanitizer = (ROOT / sanitizer_rel).read_text(encoding="utf-8")
    assert "scrubStyle(" in sanitizer, (
        "sanitizeHtml.ts writes a style attribute without scrubbing it"
    )


def test_object_and_base_uri_are_locked_down():
    """Defence in depth: <object>/<embed> and <base href> are classic bypasses.

    A permissive base-uri lets an attacker re-root every relative script URL,
    which can defeat a nonce policy that only whitelists 'self'.
    """
    d = _csp()
    obj = d.get("object-src")
    base = d.get("base-uri")
    assert obj is not None and "'none'" in obj, (
        f"object-src should be 'none' (blocks Flash/plugin-based script "
        f"execution). Got: {obj!r}"
    )
    assert base is not None and ("'self'" in base or "'none'" in base), (
        f"base-uri must be locked to 'self' or 'none' so relative script URLs "
        f"cannot be re-rooted. Got: {base!r}"
    )
    form = d.get("form-action")
    assert form is not None and "'self'" in form, (
        f"form-action must be 'self' so an injected/hijacked <form> cannot POST "
        f"credentials or CSRF tokens to a third-party origin. Got: {form!r}"
    )
